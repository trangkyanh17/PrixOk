package main

import "time"

func nextBackoff(current, minimum, maximum time.Duration) time.Duration {
	if current < minimum {
		return minimum
	}
	next := current * 2
	if next > maximum {
		return maximum
	}
	return next
}

func main() {}
