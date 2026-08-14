package main

import "time"

type repairBackoff struct {
	failures int
	nextAt   time.Time
}

func (b *repairBackoff) reset() {
	b.failures = 0
	b.nextAt = time.Time{}
}

func (b *repairBackoff) fail(now time.Time) time.Duration {
	b.failures++
	delay := repairDelay(b.failures)
	b.nextAt = now.Add(delay)
	return delay
}

func (b repairBackoff) ready(now time.Time) bool {
	return b.nextAt.IsZero() || !now.Before(b.nextAt)
}

func repairDelay(failures int) time.Duration {
	switch failures {
	case 1:
		return 30 * time.Second
	case 2:
		return 60 * time.Second
	case 3:
		return 120 * time.Second
	case 4:
		return 300 * time.Second
	default:
		return 600 * time.Second
	}
}
