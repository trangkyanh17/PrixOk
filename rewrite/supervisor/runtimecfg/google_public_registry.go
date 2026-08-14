package runtimecfg

import (
	"context"
	"fmt"
)

func (runtime GooglePublicToolRuntime) NormalizedRegisteredTools() []RegisteredTool {
	return []RegisteredTool{
		{
			Name:        "google_youtube_search",
			Declaration: GoogleYouTubeSearchDeclaration(),
			Privacy:     ToolPrivacyPublic,
			Executor: func(ctx context.Context, _ ToolContext, arguments map[string]any) (any, error) {
				return runtime.YouTubeSearch(
					ctx,
					googleString(arguments["query"]),
					googleClampInt(arguments["max_results"], 1, 10, 5),
					googleString(arguments["region_code"]),
				), nil
			},
		},
		{
			Name:        "google_safe_browsing",
			Declaration: GoogleSafeBrowsingDeclaration(),
			Privacy:     ToolPrivacyPublic,
			Executor: func(ctx context.Context, _ ToolContext, arguments map[string]any) (any, error) {
				return runtime.SafeBrowsing(ctx, googleStringSlice(arguments["urls"])), nil
			},
		},
		{
			Name:        "google_books_search",
			Declaration: GoogleBooksSearchDeclaration(),
			Privacy:     ToolPrivacyPublic,
			Executor: func(ctx context.Context, _ ToolContext, arguments map[string]any) (any, error) {
				return runtime.BooksSearch(
					ctx,
					googleString(arguments["query"]),
					googleClampInt(arguments["max_results"], 1, 10, 5),
				), nil
			},
		},
	}
}

func RegisterNormalizedGooglePublicTools(registry *ToolRegistry, runtime GooglePublicToolRuntime) error {
	if registry == nil {
		return fmt.Errorf("tool registry is nil")
	}
	for _, tool := range runtime.NormalizedRegisteredTools() {
		if err := registry.Register(tool); err != nil {
			return err
		}
	}
	return nil
}
