package runtimecfg

import (
	"context"
	"fmt"
	"net/http"
	"net/url"
	"strings"
)

const (
	DefaultGooglePlacesSearchURL = "https://places.googleapis.com/v1/places:searchText"
	DefaultGoogleRoutesURL       = "https://routes.googleapis.com/directions/v2:computeRoutes"
	DefaultGoogleGeocodeURL      = "https://maps.googleapis.com/maps/api/geocode/json"
)

type GoogleMapsToolRuntime struct {
	Client          HTTPDoer
	Values          map[string]string
	PlacesSearchURL string
	RoutesURL       string
	GeocodeURL      string
}

func (runtime GoogleMapsToolRuntime) setting(names ...string) string {
	return (GooglePublicToolRuntime{Values: runtime.Values}).setting(names...)
}

func GooglePlacesSearchDeclaration() map[string]any {
	return map[string]any{
		"name":        "google_places_search",
		"description": "Tìm địa điểm, cửa hàng, nhà hàng, khách sạn và POI bằng Google Places.",
		"parameters": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"query":         map[string]any{"type": "string", "description": "Mô tả địa điểm cần tìm."},
				"max_results":   map[string]any{"type": "integer", "minimum": 1, "maximum": 10},
				"latitude":      map[string]any{"type": "number"},
				"longitude":     map[string]any{"type": "number"},
				"radius_meters": map[string]any{"type": "number"},
			},
			"required": []any{"query"},
		},
	}
}

func GoogleRouteDeclaration() map[string]any {
	return map[string]any{
		"name":        "google_route",
		"description": "Tính quãng đường và thời gian di chuyển bằng Google Routes. Dùng khi hỏi đường, khoảng cách hoặc thời gian đi.",
		"parameters": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"origin":                map[string]any{"type": "string"},
				"destination":           map[string]any{"type": "string"},
				"origin_latitude":       map[string]any{"type": "number"},
				"origin_longitude":      map[string]any{"type": "number"},
				"destination_latitude":  map[string]any{"type": "number"},
				"destination_longitude": map[string]any{"type": "number"},
				"travel_mode": map[string]any{
					"type": "string",
					"enum": []any{"DRIVE", "TWO_WHEELER", "BICYCLE", "WALK", "TRANSIT"},
				},
			},
			"required": []any{},
		},
	}
}

func GoogleGeocodeDeclaration() map[string]any {
	return map[string]any{
		"name":        "google_geocode",
		"description": "Chuyển địa chỉ/tên địa điểm thành tọa độ và địa chỉ chuẩn bằng Google Geocoding.",
		"parameters": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"address":  map[string]any{"type": "string"},
				"language": map[string]any{"type": "string"},
			},
			"required": []any{"address"},
		},
	}
}

func googleString(value any) string {
	if value == nil {
		return ""
	}
	text := strings.TrimSpace(fmt.Sprint(value))
	if text == "<nil>" {
		return ""
	}
	return text
}

func googleOptionalFloat(value any) (*float64, bool) {
	switch current := value.(type) {
	case float64:
		return &current, true
	case float32:
		parsed := float64(current)
		return &parsed, true
	case int:
		parsed := float64(current)
		return &parsed, true
	case int64:
		parsed := float64(current)
		return &parsed, true
	case string:
		var parsed float64
		if _, err := fmt.Sscan(strings.TrimSpace(current), &parsed); err == nil {
			return &parsed, true
		}
	}
	return nil, false
}

func googleRadius(value any) float64 {
	radius := 5000.0
	if parsed, ok := googleOptionalFloat(value); ok {
		radius = *parsed
	}
	if radius < 100 {
		return 100
	}
	if radius > 50000 {
		return 50000
	}
	return radius
}

func (runtime GoogleMapsToolRuntime) PlacesSearch(
	ctx context.Context,
	query string,
	maxResults int,
	latitude any,
	longitude any,
	radiusMeters any,
) map[string]any {
	key := runtime.setting("GOOGLE_MAPS_API_KEY", "GOOGLE_API_KEY")
	if key == "" {
		return googleToolError("Thiếu GOOGLE_MAPS_API_KEY hoặc GOOGLE_API_KEY.", "NOT_CONFIGURED")
	}
	query = strings.TrimSpace(query)
	if query == "" {
		return googleToolError("Query địa điểm rỗng.", "")
	}

	region := strings.ToUpper(runtime.setting("GOOGLE_DEFAULT_REGION"))
	if region == "" {
		region = "VN"
	}
	if len(region) > 2 {
		region = region[:2]
	}

	body := map[string]any{
		"textQuery":    query,
		"pageSize":     googleClampInt(maxResults, 1, 10, 5),
		"languageCode": "vi",
		"regionCode":   region,
	}

	lat, latOK := googleOptionalFloat(latitude)
	lon, lonOK := googleOptionalFloat(longitude)
	if latOK && lonOK {
		body["locationBias"] = map[string]any{
			"circle": map[string]any{
				"center": map[string]any{
					"latitude":  *lat,
					"longitude": *lon,
				},
				"radius": googleRadius(radiusMeters),
			},
		}
	}

	fieldMask := strings.Join([]string{
		"places.id",
		"places.displayName",
		"places.formattedAddress",
		"places.location",
		"places.googleMapsUri",
		"places.rating",
		"places.userRatingCount",
	}, ",")

	payload, err := googleJSONRequest(
		ctx,
		runtime.Client,
		http.MethodPost,
		googleEndpoint(runtime.PlacesSearchURL, DefaultGooglePlacesSearchURL),
		map[string]string{
			"Content-Type":     "application/json",
			"X-Goog-Api-Key":   key,
			"X-Goog-FieldMask": fieldMask,
		},
		nil,
		body,
	)
	if err != nil {
		return googleToolError(err.Error(), "")
	}

	results := []any{}
	for _, rawPlace := range weatherSlice(payload["places"]) {
		place := weatherMap(rawPlace)
		displayName := weatherMap(place["displayName"])
		var name any = place["displayName"]
		if len(displayName) > 0 {
			name = displayName["text"]
		}
		results = append(results, map[string]any{
			"place_id":          place["id"],
			"name":              name,
			"address":           place["formattedAddress"],
			"location":          place["location"],
			"rating":            place["rating"],
			"user_rating_count": place["userRatingCount"],
			"google_maps_url":   place["googleMapsUri"],
		})
	}
	return googleToolOK(map[string]any{
		"source":  "Google Places API (New)",
		"query":   query,
		"results": results,
	})
}

func googleWaypoint(text string, latitude any, longitude any) map[string]any {
	lat, latOK := googleOptionalFloat(latitude)
	lon, lonOK := googleOptionalFloat(longitude)
	if latOK && lonOK {
		return map[string]any{
			"location": map[string]any{
				"latLng": map[string]any{
					"latitude":  *lat,
					"longitude": *lon,
				},
			},
		}
	}
	text = strings.TrimSpace(text)
	if text == "" {
		return nil
	}
	return map[string]any{"address": text}
}

func (runtime GoogleMapsToolRuntime) RouteLookup(
	ctx context.Context,
	origin string,
	destination string,
	originLatitude any,
	originLongitude any,
	destinationLatitude any,
	destinationLongitude any,
	travelMode string,
) map[string]any {
	key := runtime.setting("GOOGLE_MAPS_API_KEY", "GOOGLE_API_KEY")
	if key == "" {
		return googleToolError("Thiếu GOOGLE_MAPS_API_KEY hoặc GOOGLE_API_KEY.", "NOT_CONFIGURED")
	}

	originWaypoint := googleWaypoint(origin, originLatitude, originLongitude)
	destinationWaypoint := googleWaypoint(destination, destinationLatitude, destinationLongitude)
	if originWaypoint == nil || destinationWaypoint == nil {
		return googleToolError("Cần đủ điểm xuất phát và điểm đến.", "")
	}

	mode := strings.ToUpper(strings.TrimSpace(travelMode))
	switch mode {
	case "DRIVE", "TWO_WHEELER", "BICYCLE", "WALK", "TRANSIT":
	default:
		mode = "DRIVE"
	}

	body := map[string]any{
		"origin":       originWaypoint,
		"destination":  destinationWaypoint,
		"travelMode":   mode,
		"languageCode": "vi",
		"units":        "METRIC",
	}
	if mode == "DRIVE" || mode == "TWO_WHEELER" {
		body["routingPreference"] = "TRAFFIC_AWARE"
	}

	payload, err := googleJSONRequest(
		ctx,
		runtime.Client,
		http.MethodPost,
		googleEndpoint(runtime.RoutesURL, DefaultGoogleRoutesURL),
		map[string]string{
			"Content-Type":     "application/json",
			"X-Goog-Api-Key":   key,
			"X-Goog-FieldMask": "routes.distanceMeters,routes.duration,routes.staticDuration",
		},
		nil,
		body,
	)
	if err != nil {
		return googleToolError(err.Error(), "")
	}
	routes := weatherSlice(payload["routes"])
	if len(routes) > 3 {
		routes = routes[:3]
	}
	return googleToolOK(map[string]any{
		"source":      "Google Routes API",
		"travel_mode": mode,
		"routes":      routes,
	})
}

func (runtime GoogleMapsToolRuntime) Geocode(
	ctx context.Context,
	address string,
	language string,
) map[string]any {
	key := runtime.setting("GOOGLE_MAPS_API_KEY", "GOOGLE_API_KEY")
	if key == "" {
		return googleToolError("Thiếu GOOGLE_MAPS_API_KEY hoặc GOOGLE_API_KEY.", "NOT_CONFIGURED")
	}
	address = strings.TrimSpace(address)
	if address == "" {
		return googleToolError("Địa chỉ rỗng.", "")
	}
	language = strings.TrimSpace(language)
	if language == "" {
		language = "vi"
	}

	payload, err := googleJSONRequest(
		ctx,
		runtime.Client,
		http.MethodGet,
		googleEndpoint(runtime.GeocodeURL, DefaultGoogleGeocodeURL),
		nil,
		url.Values{
			"address":  []string{address},
			"language": []string{language},
			"key":      []string{key},
		},
		nil,
	)
	if err != nil {
		return googleToolError(err.Error(), "")
	}
	status := googleString(payload["status"])
	if status != "" && status != "OK" {
		return googleToolError(
			fmt.Sprintf(
				"Geocoding status=%s: %s",
				status,
				googleString(payload["error_message"]),
			),
			"",
		)
	}

	results := []any{}
	for index, rawItem := range weatherSlice(payload["results"]) {
		if index >= 8 {
			break
		}
		item := weatherMap(rawItem)
		geometry := weatherMap(item["geometry"])
		results = append(results, map[string]any{
			"formatted_address": item["formatted_address"],
			"place_id":          item["place_id"],
			"location":          geometry["location"],
			"location_type":     geometry["location_type"],
			"types":             item["types"],
		})
	}
	return googleToolOK(map[string]any{
		"source":  "Google Geocoding API",
		"query":   address,
		"results": results,
	})
}

func (runtime GoogleMapsToolRuntime) RegisteredTools() []RegisteredTool {
	return []RegisteredTool{
		{
			Name:        "google_places_search",
			Declaration: GooglePlacesSearchDeclaration(),
			Privacy:     ToolPrivacyPublic,
			Executor: func(ctx context.Context, _ ToolContext, arguments map[string]any) (any, error) {
				return runtime.PlacesSearch(
					ctx,
					googleString(arguments["query"]),
					googleClampInt(arguments["max_results"], 1, 10, 5),
					arguments["latitude"],
					arguments["longitude"],
					arguments["radius_meters"],
				), nil
			},
		},
		{
			Name:        "google_route",
			Declaration: GoogleRouteDeclaration(),
			Privacy:     ToolPrivacyPublic,
			Executor: func(ctx context.Context, _ ToolContext, arguments map[string]any) (any, error) {
				return runtime.RouteLookup(
					ctx,
					googleString(arguments["origin"]),
					googleString(arguments["destination"]),
					arguments["origin_latitude"],
					arguments["origin_longitude"],
					arguments["destination_latitude"],
					arguments["destination_longitude"],
					googleString(arguments["travel_mode"]),
				), nil
			},
		},
		{
			Name:        "google_geocode",
			Declaration: GoogleGeocodeDeclaration(),
			Privacy:     ToolPrivacyPublic,
			Executor: func(ctx context.Context, _ ToolContext, arguments map[string]any) (any, error) {
				return runtime.Geocode(
					ctx,
					googleString(arguments["address"]),
					googleString(arguments["language"]),
				), nil
			},
		},
	}
}

func RegisterGoogleMapsTools(registry *ToolRegistry, runtime GoogleMapsToolRuntime) error {
	if registry == nil {
		return fmt.Errorf("tool registry is nil")
	}
	for _, tool := range runtime.RegisteredTools() {
		if err := registry.Register(tool); err != nil {
			return err
		}
	}
	return nil
}
