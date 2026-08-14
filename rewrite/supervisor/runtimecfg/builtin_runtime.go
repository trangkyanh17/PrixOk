package runtimecfg

import "strings"

type BuiltinRuntimeConfig struct {
	Values     map[string]string
	Client     HTTPDoer
	AuthClient HTTPDoer

	WeatherGeocodingURL string
	WeatherForecastURL  string
	YouTubeSearchURL    string
	SafeBrowsingURL     string
	BooksURL            string
	PlacesSearchURL     string
	RoutesURL           string
	GeocodeURL          string
	TranslationBaseURL  string
	DriveBaseURL        string
	CalendarBaseURL     string
	GmailBaseURL        string
	SheetsBaseURL       string
	TTSURL              string
	VisionURL           string
	DocumentAIBaseURL   string
	SpeechBaseURL       string
	OAuthTokenURL       string

	VoiceSender GoogleVoiceSender
}

type BuiltinRuntime struct {
	Registry    *ToolRegistry
	Credentials *ServiceAccountTokenProvider
	Workspace   GoogleAccessTokenProvider
	Audio       GoogleAudioRuntime
}

func configuredCloudCredentials(
	values map[string]string,
	authClient HTTPDoer,
) *ServiceAccountTokenProvider {
	settings := GooglePublicToolRuntime{Values: values}
	path := settings.setting("GOOGLE_APPLICATION_CREDENTIALS")
	if path == "" {
		return nil
	}
	provider := NewServiceAccountTokenProvider(path)
	provider.Client = authClient
	return provider
}

func configureWorkspaceProvider(
	provider GoogleAccessTokenProvider,
	authClient HTTPDoer,
	oauthTokenURL string,
) {
	switch value := provider.(type) {
	case *OAuthRefreshTokenProvider:
		value.Client = authClient
		if endpoint := strings.TrimSpace(oauthTokenURL); endpoint != "" {
			value.TokenURL = endpoint
		}
	case *WorkspaceServiceAccountTokenProvider:
		value.Client = authClient
	}
}

func NewConfiguredBuiltinRuntime(config BuiltinRuntimeConfig) (*BuiltinRuntime, error) {
	values := cloneStringMap(config.Values)
	authClient := config.AuthClient
	if authClient == nil {
		authClient = config.Client
	}
	credentials := configuredCloudCredentials(values, authClient)

	var workspaceProvider GoogleAccessTokenProvider
	if provider, err := NewGoogleWorkspaceTokenProvider(values); err == nil {
		workspaceProvider = provider
		configureWorkspaceProvider(
			workspaceProvider,
			authClient,
			config.OAuthTokenURL,
		)
	}

	registry, err := NewBuiltinToolRegistry(BuiltinToolOptions{
		Weather: WeatherToolRuntime{
			Client:       config.Client,
			GeocodingURL: config.WeatherGeocodingURL,
			ForecastURL:  config.WeatherForecastURL,
		},
		GooglePublic: GooglePublicToolRuntime{
			Client:           config.Client,
			Values:           values,
			YouTubeSearchURL: config.YouTubeSearchURL,
			SafeBrowsingURL:  config.SafeBrowsingURL,
			BooksURL:         config.BooksURL,
		},
		GoogleMaps: GoogleMapsToolRuntime{
			Client:          config.Client,
			Values:          values,
			PlacesSearchURL: config.PlacesSearchURL,
			RoutesURL:       config.RoutesURL,
			GeocodeURL:      config.GeocodeURL,
		},
		GoogleCloud: GoogleCloudToolRuntime{
			Client:             config.Client,
			Credentials:        credentials,
			TranslationBaseURL: config.TranslationBaseURL,
		},
		GoogleCapabilities: GoogleCapabilitiesRuntime{
			Values: values,
		},
		GoogleWorkspace: GoogleWorkspaceToolRuntime{
			Client:          config.Client,
			TokenProvider:   workspaceProvider,
			DriveBaseURL:    config.DriveBaseURL,
			CalendarBaseURL: config.CalendarBaseURL,
			GmailBaseURL:    config.GmailBaseURL,
			SheetsBaseURL:   config.SheetsBaseURL,
		},
		GoogleMedia: GoogleMediaToolRuntime{
			Client:            config.Client,
			Credentials:       credentials,
			Values:            values,
			TTSURL:            config.TTSURL,
			VisionURL:         config.VisionURL,
			DocumentAIBaseURL: config.DocumentAIBaseURL,
			VoiceSender:       config.VoiceSender,
		},
	})
	if err != nil {
		return nil, err
	}

	return &BuiltinRuntime{
		Registry:    registry,
		Credentials: credentials,
		Workspace:   workspaceProvider,
		Audio: GoogleAudioRuntime{
			Client:        config.Client,
			Credentials:   credentials,
			SpeechBaseURL: config.SpeechBaseURL,
		},
	}, nil
}
