package runtimecfg

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
)

const (
	DefaultWeatherGeocodingURL = "https://geocoding-api.open-meteo.com/v1/search"
	DefaultWeatherForecastURL  = "https://api.open-meteo.com/v1/forecast"
)

type WeatherToolRuntime struct {
	Client       HTTPDoer
	GeocodingURL string
	ForecastURL  string
}

func WeatherToolDeclaration() map[string]any {
	return map[string]any{
		"name":        "get_weather",
		"description": "Lấy thời tiết hiện tại và dự báo tại một địa điểm. Dùng khi người dùng hỏi nhiệt độ, mưa, độ ẩm hoặc gió.",
		"parameters": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"location": map[string]any{
					"type":        "string",
					"description": "Tên địa điểm, ví dụ Hà Nội hoặc Đà Nẵng.",
				},
				"forecast_days": map[string]any{
					"type":        "integer",
					"description": "Số ngày dự báo từ 1 đến 7.",
					"minimum":     1,
					"maximum":     7,
				},
			},
			"required": []any{"location"},
		},
	}
}

func WeatherDescription(code any) string {
	value, ok := weatherInt(code)
	if !ok {
		return "Không xác định"
	}
	descriptions := map[int]string{
		0:  "Trời quang",
		1:  "Chủ yếu trời quang",
		2:  "Có mây rải rác",
		3:  "Nhiều mây",
		45: "Sương mù",
		48: "Sương mù đóng băng",
		51: "Mưa phùn nhẹ",
		53: "Mưa phùn vừa",
		55: "Mưa phùn mạnh",
		61: "Mưa nhẹ",
		63: "Mưa vừa",
		65: "Mưa lớn",
		71: "Tuyết nhẹ",
		73: "Tuyết vừa",
		75: "Tuyết lớn",
		80: "Mưa rào nhẹ",
		81: "Mưa rào vừa",
		82: "Mưa rào mạnh",
		95: "Dông",
		96: "Dông kèm mưa đá nhẹ",
		99: "Dông kèm mưa đá mạnh",
	}
	if description, exists := descriptions[value]; exists {
		return description
	}
	return fmt.Sprintf("Mã thời tiết %d", value)
}

func weatherInt(value any) (int, bool) {
	switch current := value.(type) {
	case int:
		return current, true
	case int64:
		return int(current), true
	case float64:
		return int(current), true
	case json.Number:
		parsed, err := strconv.Atoi(current.String())
		return parsed, err == nil
	case string:
		parsed, err := strconv.Atoi(strings.TrimSpace(current))
		return parsed, err == nil
	default:
		return 0, false
	}
}

func weatherForecastDays(value any) int {
	days, ok := weatherInt(value)
	if !ok {
		days = 2
	}
	if days < 1 {
		return 1
	}
	if days > 7 {
		return 7
	}
	return days
}

func weatherURL(rawURL string, fallback string) string {
	value := strings.TrimSpace(rawURL)
	if value == "" {
		return fallback
	}
	return value
}

func weatherJSONGet(
	ctx context.Context,
	client HTTPDoer,
	rawURL string,
	query url.Values,
) (map[string]any, error) {
	endpoint, err := url.Parse(rawURL)
	if err != nil {
		return nil, fmt.Errorf("invalid weather endpoint: %w", err)
	}
	values := endpoint.Query()
	for key, items := range query {
		for _, item := range items {
			values.Add(key, item)
		}
	}
	endpoint.RawQuery = values.Encode()

	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint.String(), nil)
	if err != nil {
		return nil, err
	}
	if client == nil {
		client = http.DefaultClient
	}
	response, err := client.Do(request)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, 4<<20))
	if err != nil {
		return nil, err
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return nil, fmt.Errorf("HTTP %d: %s", response.StatusCode, strings.TrimSpace(string(body)))
	}
	var payload map[string]any
	if err := json.Unmarshal(body, &payload); err != nil {
		return nil, fmt.Errorf("invalid JSON: %w", err)
	}
	return payload, nil
}

func weatherSlice(value any) []any {
	items, _ := value.([]any)
	return items
}

func weatherMap(value any) map[string]any {
	item, _ := value.(map[string]any)
	if item == nil {
		return map[string]any{}
	}
	return item
}

func weatherAt(object map[string]any, field string, index int) any {
	items := weatherSlice(object[field])
	if index < 0 || index >= len(items) {
		return nil
	}
	return items[index]
}

func (runtime WeatherToolRuntime) GetWeather(
	ctx context.Context,
	location string,
	forecastDays int,
) map[string]any {
	location = strings.TrimSpace(location)
	if len([]rune(location)) < 2 {
		return map[string]any{"ok": false, "error": "Tên địa điểm không hợp lệ."}
	}
	forecastDays = weatherForecastDays(forecastDays)

	geocoding, err := weatherJSONGet(
		ctx,
		runtime.Client,
		weatherURL(runtime.GeocodingURL, DefaultWeatherGeocodingURL),
		url.Values{
			"name":     []string{location},
			"count":    []string{"1"},
			"language": []string{"vi"},
			"format":   []string{"json"},
		},
	)
	if err != nil {
		return map[string]any{"ok": false, "error": "Lỗi tìm địa điểm: " + err.Error()}
	}
	places := weatherSlice(geocoding["results"])
	if len(places) == 0 {
		return map[string]any{"ok": false, "error": "Không tìm thấy địa điểm: " + location}
	}
	place := weatherMap(places[0])
	latitude := place["latitude"]
	longitude := place["longitude"]

	forecastPayload, err := weatherJSONGet(
		ctx,
		runtime.Client,
		weatherURL(runtime.ForecastURL, DefaultWeatherForecastURL),
		url.Values{
			"latitude":      []string{fmt.Sprint(latitude)},
			"longitude":     []string{fmt.Sprint(longitude)},
			"timezone":      []string{"auto"},
			"forecast_days": []string{strconv.Itoa(forecastDays)},
			"current": []string{
				"temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,rain,weather_code,cloud_cover,wind_speed_10m",
			},
			"daily": []string{
				"weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum,wind_speed_10m_max",
			},
		},
	)
	if err != nil {
		return map[string]any{"ok": false, "error": "Lỗi lấy thời tiết: " + err.Error()}
	}

	current := weatherMap(forecastPayload["current"])
	daily := weatherMap(forecastPayload["daily"])
	dates := weatherSlice(daily["time"])
	forecast := make([]any, 0, len(dates))
	for index, date := range dates {
		forecast = append(forecast, map[string]any{
			"date":                     date,
			"condition":                WeatherDescription(weatherAt(daily, "weather_code", index)),
			"temperature_max_c":        weatherAt(daily, "temperature_2m_max", index),
			"temperature_min_c":        weatherAt(daily, "temperature_2m_min", index),
			"rain_probability_percent": weatherAt(daily, "precipitation_probability_max", index),
			"precipitation_mm":         weatherAt(daily, "precipitation_sum", index),
			"wind_speed_max_kmh":       weatherAt(daily, "wind_speed_10m_max", index),
		})
	}

	return map[string]any{
		"ok":     true,
		"source": "Open-Meteo",
		"location": map[string]any{
			"requested": location,
			"name":      place["name"],
			"admin1":    place["admin1"],
			"country":   place["country"],
			"latitude":  latitude,
			"longitude": longitude,
			"timezone":  forecastPayload["timezone"],
		},
		"current": map[string]any{
			"time":                   current["time"],
			"condition":              WeatherDescription(current["weather_code"]),
			"temperature_c":          current["temperature_2m"],
			"apparent_temperature_c": current["apparent_temperature"],
			"humidity_percent":       current["relative_humidity_2m"],
			"precipitation_mm":       current["precipitation"],
			"rain_mm":                current["rain"],
			"cloud_cover_percent":    current["cloud_cover"],
			"wind_speed_kmh":         current["wind_speed_10m"],
		},
		"forecast": forecast,
	}
}

func (runtime WeatherToolRuntime) RegisteredTool() RegisteredTool {
	return RegisteredTool{
		Name:        "get_weather",
		Declaration: WeatherToolDeclaration(),
		Privacy:     ToolPrivacyPublic,
		Executor: func(ctx context.Context, _ ToolContext, arguments map[string]any) (any, error) {
			return runtime.GetWeather(
				ctx,
				fmt.Sprint(arguments["location"]),
				weatherForecastDays(arguments["forecast_days"]),
			), nil
		},
	}
}

func RegisterWeatherTool(registry *ToolRegistry, runtime WeatherToolRuntime) error {
	if registry == nil {
		return fmt.Errorf("tool registry is nil")
	}
	return registry.Register(runtime.RegisteredTool())
}
