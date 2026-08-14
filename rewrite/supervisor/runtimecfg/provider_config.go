package runtimecfg

import (
	"os"
	"strings"
	"sync"
)

const DefaultProviderEnvPath = "/home/prix/secrets/prixok/free-providers.env"

var ProviderKeyNames = map[string]string{
	"cerebras":   "CEREBRAS_API_KEY",
	"groq":       "GROQ_API_KEY",
	"openrouter": "OPENROUTER_API_KEY",
}

var providerEnvPrefixes = []string{
	"ATRI_FREE_",
	"CEREBRAS_",
	"GROQ_",
	"OPENROUTER_",
}

type ProviderConfigCache struct {
	mu      sync.Mutex
	path    string
	mtimeNS int64
	values  map[string]string
}

func NewProviderConfigCache() *ProviderConfigCache {
	return &ProviderConfigCache{mtimeNS: -1, values: map[string]string{}}
}

func ProviderEnvPath(configured string) string {
	configured = strings.TrimSpace(configured)
	if configured != "" {
		return configured
	}
	return DefaultProviderEnvPath
}

func cloneStringMap(values map[string]string) map[string]string {
	cloned := make(map[string]string, len(values))
	for key, value := range values {
		cloned[key] = value
	}
	return cloned
}

func parseProviderEnv(data string) map[string]string {
	values := map[string]string{}
	for _, rawLine := range strings.Split(strings.ReplaceAll(data, "\r\n", "\n"), "\n") {
		line := strings.TrimSpace(rawLine)
		if line == "" || strings.HasPrefix(line, "#") || !strings.Contains(line, "=") {
			continue
		}
		parts := strings.SplitN(line, "=", 2)
		key := strings.TrimSpace(parts[0])
		value := strings.TrimSpace(parts[1])
		if key == "" {
			continue
		}
		if len(value) >= 2 && value[0] == value[len(value)-1] && (value[0] == '\'' || value[0] == '"') {
			value = value[1 : len(value)-1]
		}
		values[key] = value
	}
	return values
}

func (cache *ProviderConfigCache) readFile(path string) map[string]string {
	cache.mu.Lock()
	defer cache.mu.Unlock()

	info, err := os.Stat(path)
	if err != nil {
		cache.path = path
		cache.mtimeNS = -1
		cache.values = map[string]string{}
		return map[string]string{}
	}
	mtimeNS := info.ModTime().UnixNano()
	if path == cache.path && mtimeNS == cache.mtimeNS {
		return cloneStringMap(cache.values)
	}

	data, err := os.ReadFile(path)
	if err != nil {
		data = nil
	}
	values := parseProviderEnv(string(data))
	cache.path = path
	cache.mtimeNS = mtimeNS
	cache.values = cloneStringMap(values)
	return cloneStringMap(values)
}

func providerEnvAllowed(key string) bool {
	for _, prefix := range providerEnvPrefixes {
		if strings.HasPrefix(key, prefix) {
			return true
		}
	}
	return false
}

func (cache *ProviderConfigCache) Load(path string, environment map[string]string) map[string]string {
	values := cache.readFile(ProviderEnvPath(path))
	for key, value := range environment {
		if providerEnvAllowed(key) {
			values[key] = value
		}
	}
	return values
}

func ProviderAPIKeys(values map[string]string) map[string]string {
	keys := make(map[string]string, len(ProviderKeyNames))
	for provider, keyName := range ProviderKeyNames {
		keys[provider] = strings.TrimSpace(values[keyName])
	}
	return keys
}

func (cache *ProviderConfigCache) Reset() {
	cache.mu.Lock()
	defer cache.mu.Unlock()
	cache.path = ""
	cache.mtimeNS = -1
	cache.values = map[string]string{}
}
