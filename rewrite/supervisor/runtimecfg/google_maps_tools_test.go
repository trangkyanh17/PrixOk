package runtimecfg

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestGoogleMapsPlacesRoutesAndGeocode(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/places":
			if r.Method != http.MethodPost {
				t.Fatalf("places method=%s", r.Method)
			}
			if r.Header.Get("X-Goog-Api-Key") != "maps-key" {
				t.Fatalf("places key=%q", r.Header.Get("X-Goog-Api-Key"))
			}
			if !strings.Contains(r.Header.Get("X-Goog-FieldMask"), "places.googleMapsUri") {
				t.Fatalf("places field mask=%q", r.Header.Get("X-Goog-FieldMask"))
			}
			var body map[string]any
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
				t.Fatal(err)
			}
			if body["textQuery"] != "phở gần đây" || body["pageSize"] != float64(10) || body["regionCode"] != "VN" {
				t.Fatalf("places body=%v", body)
			}
			bias := body["locationBias"].(map[string]any)
			circle := bias["circle"].(map[string]any)
			if circle["radius"] != 50000.0 {
				t.Fatalf("places radius=%v", circle["radius"])
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"places": []any{
					map[string]any{
						"id":               "place-1",
						"displayName":      map[string]any{"text": "Phở Prix"},
						"formattedAddress": "Hà Nội",
						"location":         map[string]any{"latitude": 21.0, "longitude": 105.8},
						"googleMapsUri":    "https://maps.example/place-1",
						"rating":           4.8,
						"userRatingCount":  123,
					},
				},
			})
		case "/routes":
			if r.Method != http.MethodPost {
				t.Fatalf("routes method=%s", r.Method)
			}
			if r.Header.Get("X-Goog-Api-Key") != "maps-key" {
				t.Fatalf("routes key=%q", r.Header.Get("X-Goog-Api-Key"))
			}
			var body map[string]any
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
				t.Fatal(err)
			}
			if body["travelMode"] != "TWO_WHEELER" || body["routingPreference"] != "TRAFFIC_AWARE" || body["units"] != "METRIC" {
				t.Fatalf("routes body=%v", body)
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"routes": []any{
					map[string]any{"distanceMeters": 1200, "duration": "420s", "staticDuration": "360s"},
					map[string]any{"distanceMeters": 1300, "duration": "450s", "staticDuration": "390s"},
					map[string]any{"distanceMeters": 1400, "duration": "480s", "staticDuration": "410s"},
					map[string]any{"distanceMeters": 1500, "duration": "500s", "staticDuration": "430s"},
				},
			})
		case "/geocode":
			if r.URL.Query().Get("address") != "Hồ Gươm" || r.URL.Query().Get("language") != "vi" || r.URL.Query().Get("key") != "maps-key" {
				t.Fatalf("geocode query=%v", r.URL.Query())
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"status": "OK",
				"results": []any{
					map[string]any{
						"formatted_address": "Hoàn Kiếm, Hà Nội",
						"place_id":          "geo-1",
						"geometry": map[string]any{
							"location":      map[string]any{"lat": 21.0287, "lng": 105.8521},
							"location_type": "APPROXIMATE",
						},
						"types": []any{"point_of_interest"},
					},
				},
			})
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()

	runtime := GoogleMapsToolRuntime{
		Client: server.Client(),
		Values: map[string]string{
			"GOOGLE_MAPS_API_KEY":   "maps-key",
			"GOOGLE_DEFAULT_REGION": "VNM",
		},
		PlacesSearchURL: server.URL + "/places",
		RoutesURL:       server.URL + "/routes",
		GeocodeURL:      server.URL + "/geocode",
	}

	places := runtime.PlacesSearch(context.Background(), " phở gần đây ", 99, 21.0285, 105.8542, 99000)
	if places["ok"] != true || places["source"] != "Google Places API (New)" {
		t.Fatalf("places=%v", places)
	}
	placeResults := places["results"].([]any)
	if len(placeResults) != 1 || placeResults[0].(map[string]any)["name"] != "Phở Prix" {
		t.Fatalf("place results=%v", placeResults)
	}

	routes := runtime.RouteLookup(
		context.Background(),
		"",
		"",
		21.0285,
		105.8542,
		21.03,
		105.86,
		"TWO_WHEELER",
	)
	if routes["ok"] != true || routes["travel_mode"] != "TWO_WHEELER" {
		t.Fatalf("routes=%v", routes)
	}
	if len(routes["routes"].([]any)) != 3 {
		t.Fatalf("route cap=%v", routes["routes"])
	}

	geocode := runtime.Geocode(context.Background(), " Hồ Gươm ", "")
	if geocode["ok"] != true || geocode["source"] != "Google Geocoding API" {
		t.Fatalf("geocode=%v", geocode)
	}
	geoResults := geocode["results"].([]any)
	if len(geoResults) != 1 || geoResults[0].(map[string]any)["place_id"] != "geo-1" {
		t.Fatalf("geo results=%v", geoResults)
	}
}

func TestGoogleMapsValidationAndStatusErrors(t *testing.T) {
	noKey := GoogleMapsToolRuntime{}
	if result := noKey.PlacesSearch(context.Background(), "x", 5, nil, nil, nil); result["code"] != "NOT_CONFIGURED" {
		t.Fatalf("places no key=%v", result)
	}
	if result := noKey.RouteLookup(context.Background(), "", "", nil, nil, nil, nil, "DRIVE"); result["code"] != "NOT_CONFIGURED" {
		t.Fatalf("route no key=%v", result)
	}
	if result := noKey.Geocode(context.Background(), "x", "vi"); result["code"] != "NOT_CONFIGURED" {
		t.Fatalf("geocode no key=%v", result)
	}

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{
			"status":        "ZERO_RESULTS",
			"error_message": "none",
		})
	}))
	defer server.Close()

	runtime := GoogleMapsToolRuntime{
		Client:     server.Client(),
		Values:     map[string]string{"GOOGLE_API_KEY": "fallback-key"},
		GeocodeURL: server.URL,
	}
	result := runtime.Geocode(context.Background(), "Nowhere", "en")
	if result["ok"] != false || !strings.Contains(result["error"].(string), "ZERO_RESULTS") {
		t.Fatalf("geocode status=%v", result)
	}
}

func TestRegisterGoogleMapsTools(t *testing.T) {
	registry := NewToolRegistry()
	if err := RegisterGoogleMapsTools(registry, GoogleMapsToolRuntime{}); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"google_places_search", "google_route", "google_geocode"} {
		if !registry.Has(name) {
			t.Fatalf("missing tool %s", name)
		}
	}
	if declarations := registry.Declarations("chat", false); len(declarations) != 3 {
		t.Fatalf("declarations=%v", declarations)
	}

	route := registry.Execute(
		context.Background(),
		ToolContext{Mode: "chat"},
		"google_route",
		map[string]any{},
		false,
	).(map[string]any)
	if route["code"] != "NOT_CONFIGURED" {
		t.Fatalf("route=%v", route)
	}
}
