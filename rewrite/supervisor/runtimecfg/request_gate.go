package runtimecfg

import (
	"sort"
	"sync"
)

type ChatRuntimeKey struct {
	ChatID   int64
	ThreadID int64
}

type RuntimeSweepResult struct {
	EvictedChats []ChatRuntimeKey
	StaleUsers   []int64
	Swept        bool
}

type RuntimeStateTracker struct {
	mu sync.Mutex

	MaxRuntimeChats int
	TTLSeconds      float64
	LastSweep       float64
	LastSeen        map[ChatRuntimeKey]float64
	LastRequestAt   map[int64]float64
}

func NewRuntimeStateTracker(maxRuntimeChats int, ttlSeconds float64) *RuntimeStateTracker {
	if maxRuntimeChats < 10 {
		maxRuntimeChats = 10
	}
	if ttlSeconds < 300 {
		ttlSeconds = 300
	}
	return &RuntimeStateTracker{
		MaxRuntimeChats: maxRuntimeChats,
		TTLSeconds:      ttlSeconds,
		LastSeen:        map[ChatRuntimeKey]float64{},
		LastRequestAt:   map[int64]float64{},
	}
}

// ensureLocked normalizes state. Callers must hold state.mu.
func (state *RuntimeStateTracker) ensureLocked() {
	if state.MaxRuntimeChats < 10 {
		state.MaxRuntimeChats = 10
	}
	if state.TTLSeconds < 300 {
		state.TTLSeconds = 300
	}
	if state.LastSeen == nil {
		state.LastSeen = map[ChatRuntimeKey]float64{}
	}
	if state.LastRequestAt == nil {
		state.LastRequestAt = map[int64]float64{}
	}
}

func (state *RuntimeStateTracker) Touch(
	key ChatRuntimeKey,
	now float64,
	isLocked func(ChatRuntimeKey) bool,
) RuntimeSweepResult {
	if state == nil {
		return RuntimeSweepResult{}
	}
	state.mu.Lock()
	defer state.mu.Unlock()
	state.ensureLocked()
	state.LastSeen[key] = now

	if now-state.LastSweep < 60 && len(state.LastSeen) <= state.MaxRuntimeChats {
		return RuntimeSweepResult{}
	}

	state.LastSweep = now
	staleBefore := now - state.TTLSeconds
	type seenItem struct {
		Key      ChatRuntimeKey
		LastSeen float64
	}
	ordered := make([]seenItem, 0, len(state.LastSeen))
	for candidate, lastSeen := range state.LastSeen {
		ordered = append(ordered, seenItem{Key: candidate, LastSeen: lastSeen})
	}
	sort.SliceStable(ordered, func(i, j int) bool {
		if ordered[i].LastSeen == ordered[j].LastSeen {
			if ordered[i].Key.ChatID == ordered[j].Key.ChatID {
				return ordered[i].Key.ThreadID < ordered[j].Key.ThreadID
			}
			return ordered[i].Key.ChatID < ordered[j].Key.ChatID
		}
		return ordered[i].LastSeen < ordered[j].LastSeen
	})

	result := RuntimeSweepResult{Swept: true}
	for _, item := range ordered {
		candidate := item.Key
		if candidate == key {
			continue
		}
		if isLocked != nil && isLocked(candidate) {
			continue
		}
		if item.LastSeen >= staleBefore && len(state.LastSeen) <= state.MaxRuntimeChats {
			break
		}
		delete(state.LastSeen, candidate)
		result.EvictedChats = append(result.EvictedChats, candidate)
	}

	if len(state.LastRequestAt) > state.MaxRuntimeChats*4 {
		cutoff := now - 60
		users := make([]int64, 0)
		for userID, requestedAt := range state.LastRequestAt {
			if requestedAt < cutoff {
				users = append(users, userID)
			}
		}
		sort.Slice(users, func(i, j int) bool { return users[i] < users[j] })
		for _, userID := range users {
			delete(state.LastRequestAt, userID)
		}
		result.StaleUsers = users
	}

	return result
}

func (state *RuntimeStateTracker) ConsumeUserCooldown(
	userID int64,
	now float64,
	cooldownSeconds float64,
	force bool,
) bool {
	if state == nil {
		return false
	}
	state.mu.Lock()
	defer state.mu.Unlock()
	state.ensureLocked()
	previous := state.LastRequestAt[userID]
	if !force && now-previous < cooldownSeconds {
		return false
	}
	state.LastRequestAt[userID] = now
	return true
}

func (state *RuntimeStateTracker) ClearUserCooldown(userID int64) {
	if state == nil {
		return
	}
	state.mu.Lock()
	defer state.mu.Unlock()
	state.ensureLocked()
	delete(state.LastRequestAt, userID)
}

type SlidingWindowQuota struct {
	mu            sync.Mutex
	Limit         int
	WindowSeconds float64
	requestTimes  []float64
}

func NewSlidingWindowQuota(limit int, windowSeconds float64) *SlidingWindowQuota {
	if limit < 1 {
		limit = 1
	}
	if windowSeconds <= 0 {
		windowSeconds = 60
	}
	return &SlidingWindowQuota{Limit: limit, WindowSeconds: windowSeconds}
}

func (quota *SlidingWindowQuota) Consume(now float64) bool {
	quota.mu.Lock()
	defer quota.mu.Unlock()
	if quota.Limit < 1 {
		quota.Limit = 1
	}
	if quota.WindowSeconds <= 0 {
		quota.WindowSeconds = 60
	}
	cut := 0
	for cut < len(quota.requestTimes) && now-quota.requestTimes[cut] >= quota.WindowSeconds {
		cut++
	}
	if cut > 0 {
		quota.requestTimes = append([]float64(nil), quota.requestTimes[cut:]...)
	}
	if len(quota.requestTimes) >= quota.Limit {
		return false
	}
	quota.requestTimes = append(quota.requestTimes, now)
	return true
}

func (quota *SlidingWindowQuota) Count(now float64) int {
	quota.mu.Lock()
	defer quota.mu.Unlock()
	cut := 0
	for cut < len(quota.requestTimes) && now-quota.requestTimes[cut] >= quota.WindowSeconds {
		cut++
	}
	if cut > 0 {
		quota.requestTimes = append([]float64(nil), quota.requestTimes[cut:]...)
	}
	return len(quota.requestTimes)
}
