package runtimecfg

import "fmt"

type BuiltinToolOptions struct {
	Weather      WeatherToolRuntime
	GooglePublic GooglePublicToolRuntime
	GoogleMaps   GoogleMapsToolRuntime
	GoogleCloud  GoogleCloudToolRuntime
}

func RegisterBuiltinTools(registry *ToolRegistry, options BuiltinToolOptions) error {
	if registry == nil {
		return fmt.Errorf("tool registry is nil")
	}
	if err := RegisterWeatherTool(registry, options.Weather); err != nil {
		return err
	}
	if err := RegisterGooglePublicTools(registry, options.GooglePublic); err != nil {
		return err
	}
	if err := RegisterGoogleMapsTools(registry, options.GoogleMaps); err != nil {
		return err
	}
	if err := RegisterGoogleCloudTools(registry, options.GoogleCloud); err != nil {
		return err
	}
	return nil
}

func NewBuiltinToolRegistry(options BuiltinToolOptions) (*ToolRegistry, error) {
	registry := NewToolRegistry()
	if err := RegisterBuiltinTools(registry, options); err != nil {
		return nil, err
	}
	return registry, nil
}
