package runtimecfg

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestWeatherDescriptionParity(t *testing.T) {
	cases := map[any]string{
		0:     "Trời quang",
		63:    "Mưa vừa",
		95.0:  "Dông",
		"99":  "Dông kèm mưa đá mạnh",
		12345: "Mã thời tiết 12345",
	}
	for input, want := range cases {
		if got := WeatherDescription(input); got != want {
			t.Fatalf("WeatherDescription(%v)=%q want=%q", input, got, want)
		}
	}
	if got := WeatherDescription(nil); got != "Không xác định" {
		t.Fatalf("nil description=%q", got)
	}
}

func TestWeatherToolRuntimeReturnsOpenMeteoShape(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/geo":
			if r.URL.Query().Get("name") != "Hà Nội" || r.URL.Query().Get("language") != "vi" {
				t.Fatalf("geo query=%v", r.URL.Query())
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"results": []any{
					map[string]any{
						"name":      "Hà Nội",
						"admin1":    "Hanoi",
						"country":   "Việt Nam",
						"latitude":  21.0285,
						"longitude": 105.8542,
					},
				},
			})
		case "/forecast":
			if r.URL.Query().Get("forecast_days") != "2" || r.URL.Query().Get("timezone") != "auto" {
				t.Fatalf("forecast query=%v", r.URL.Query())
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"timezone": "Asia/Bangkok",
				"current": map[string]any{
					"time":                 "2026-08-14T10:00",
					"weather_code":         63,
					"temperature_2m":       31.2,
					"apparent_temperature": 36.1,
					"relative_humidity_2m": 72,
					"precipitation":        1.2,
					"rain":                 1.2,
					"cloud_cover":          88,
					"wind_speed_10m":       8.4,
				},
				"daily": map[string]any{
					"time":                          []any{"2026-08-14", "2026-08-15"},
					"weather_code":                  []any{63, 80},
					"temperature_2m_max":            []any{33.1, 32.0},
					"temperature_2m_min":            []any{26.0, 25.5},
					"precipitation_probability_max": []any{70, 60},
					"precipitation_sum":             []any{8.2, 5.1},
					"wind_speed_10m_max":            []any{18.0, 20.0},
				},
			})
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()

	runtime := WeatherToolRuntime{
		Client:       server.Client(),
		GeocodingURL: server.URL + "/geo",
		ForecastURL:  server.URL + "/forecast",
	}
	result := runtime.GetWeather(context.Background(), " Hà Nội ", 2)
	if result["ok"] != true || result["source"] != "Open-Meteo" {
		t.Fatalf("result=%v", result)
	}
	location := result["location"].(map[string]any)
	if location["requested"] != "Hà Nội" || location["timezone"] != "Asia/Bangkok" {
		t.Fatalf("location=%v", location)
	}
	current := result["current"].(map[string]any)
	if current["condition"] != "Mưa vừa" || current["temperature_c"] != 31.2 {
		t.Fatalf("current=%v", current)
	}
	forecast := result["forecast"].([]any)
	if len(forecast) != 2 || forecast[1].(map[string]any)["condition"] != "Mưa rào nhẹ" {
		t.Fatalf("forecast=%v", forecast)
	}
}

func TestWeatherToolErrorsAndRegistryBridge(t *testing.T) {
	if result := (WeatherToolRuntime{}).GetWeather(context.Background(), "x", 2); result["ok"] != false {
		t.Fatalf("invalid location=%v", result)
	}

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.Contains(r.URL.Path, "geo") {
			_, _ = w.Write([]byte(`{"results":[]}`))
			return
		}
		http.NotFound(w, r)
	}))
	defer server.Close()

	registry := NewToolRegistry()
	runtime := WeatherToolRuntime{
		Client:       server.Client(),
		GeocodingURL: server.URL + "/geo",
		ForecastURL:  server.URL + "/forecast",
	}
	if err := RegisterWeatherTool(registry, runtime); err != nil {
		t.Fatal(err)
	}
	if !registry.Has("get_weather") {
		t.Fatal("weather tool not registered")
	}
	declarations := registry.Declarations("chat", false)
	if len(declarations) != 1 || declarations[0]["name"] != "get_weather" {
		t.Fatalf("declarations=%v", declarations)
	}
	value := registry.Execute(
		context.Background(),
		ToolContext{Mode: "chat"},
		"get_weather",
		map[string]any{"location": "Nowhere", "forecast_days": 99},
		false,
	).(map[string]any)
	if value["ok"] != false || !strings.Contains(value["error"].(string), "Không tìm thấy địa điểm") {
		t.Fatalf("value=%v", value)
	}
}
