package runtimecfg

import (
	"reflect"
	"sync"
	"testing"
)

func TestRuntimeStateTouchSkipsSweepWithinMinuteAndBound(t *testing.T) {
	state := NewRuntimeStateTracker(10, 300)
	key := ChatRuntimeKey{ChatID: 1}
	result := state.Touch(key, 100, nil)
	if !result.Swept {
		t.Fatal("first touch should sweep because last sweep is zero and now >= 60")
	}
	result = state.Touch(ChatRuntimeKey{ChatID: 2}, 120, nil)
	if result.Swept {
		t.Fatal("bounded touch inside 60s should not sweep")
	}
}

func TestRuntimeStateTouchEvictsStaleAndPreservesCurrentLocked(t *testing.T) {
	state := NewRuntimeStateTracker(10, 300)
	current := ChatRuntimeKey{ChatID: 99}
	locked := ChatRuntimeKey{ChatID: 2}
	state.LastSweep = 100
	state.LastSeen[ChatRuntimeKey{ChatID: 1}] = 100
	state.LastSeen[locked] = 110
	state.LastSeen[ChatRuntimeKey{ChatID: 3}] = 450
	state.LastSeen[current] = 120

	result := state.Touch(current, 500, func(key ChatRuntimeKey) bool {
		return key == locked
	})
	if !result.Swept {
		t.Fatal("expected sweep")
	}
	if !reflect.DeepEqual(result.EvictedChats, []ChatRuntimeKey{{ChatID: 1}}) {
		t.Fatalf("evicted=%v", result.EvictedChats)
	}
	if _, ok := state.LastSeen[current]; !ok {
		t.Fatal("current chat was evicted")
	}
	if _, ok := state.LastSeen[locked]; !ok {
		t.Fatal("locked chat was evicted")
	}
}

func TestRuntimeStateTouchEvictsOldestToBound(t *testing.T) {
	state := NewRuntimeStateTracker(10, 1000)
	state.LastSweep = 100
	for index := int64(1); index <= 11; index++ {
		state.LastSeen[ChatRuntimeKey{ChatID: index}] = 190 + float64(index)
	}
	current := ChatRuntimeKey{ChatID: 12}
	result := state.Touch(current, 200, nil)
	if !result.Swept {
		t.Fatal("oversized state should sweep even within 60 seconds")
	}
	if len(state.LastSeen) != 10 {
		t.Fatalf("size=%d want=10", len(state.LastSeen))
	}
	if len(result.EvictedChats) != 2 || result.EvictedChats[0].ChatID != 1 || result.EvictedChats[1].ChatID != 2 {
		t.Fatalf("evicted=%v", result.EvictedChats)
	}
}

func TestRuntimeStatePrunesOldUserCooldownsOnlyWhenLarge(t *testing.T) {
	state := NewRuntimeStateTracker(10, 300)
	state.LastSweep = 0
	for userID := int64(1); userID <= 41; userID++ {
		state.LastRequestAt[userID] = 10
	}
	state.LastRequestAt[42] = 190
	result := state.Touch(ChatRuntimeKey{ChatID: 1}, 200, nil)
	if len(result.StaleUsers) != 41 {
		t.Fatalf("stale users=%d", len(result.StaleUsers))
	}
	if _, ok := state.LastRequestAt[42]; !ok {
		t.Fatal("recent user was pruned")
	}
}

func TestUserCooldownAndForceReply(t *testing.T) {
	state := NewRuntimeStateTracker(10, 300)
	if !state.ConsumeUserCooldown(7, 100, 3, false) {
		t.Fatal("first request should pass")
	}
	if state.ConsumeUserCooldown(7, 102, 3, false) {
		t.Fatal("request inside cooldown should be rejected")
	}
	if !state.ConsumeUserCooldown(7, 102, 3, true) {
		t.Fatal("forced request should bypass cooldown")
	}
	if got := state.LastRequestAt[7]; got != 102 {
		t.Fatalf("last request=%v", got)
	}
	state.ClearUserCooldown(7)
	if _, ok := state.LastRequestAt[7]; ok {
		t.Fatal("cooldown was not cleared")
	}
}

func TestRuntimeStateTrackerConcurrentAccess(t *testing.T) {
	state := NewRuntimeStateTracker(32, 300)
	var wg sync.WaitGroup
	for worker := int64(0); worker < 16; worker++ {
		worker := worker
		wg.Add(1)
		go func() {
			defer wg.Done()
			for step := int64(0); step < 100; step++ {
				now := float64(1000 + worker*100 + step)
				state.Touch(ChatRuntimeKey{ChatID: worker + 1, ThreadID: step % 3}, now, nil)
				state.ConsumeUserCooldown(worker+1, now, 1, step%7 == 0)
				if step%11 == 0 {
					state.ClearUserCooldown(worker + 1)
				}
			}
		}()
	}
	wg.Wait()
	state.mu.Lock()
	defer state.mu.Unlock()
	if len(state.LastSeen) > state.MaxRuntimeChats {
		t.Fatalf("runtime chats=%d max=%d", len(state.LastSeen), state.MaxRuntimeChats)
	}
}

func TestSlidingWindowQuota(t *testing.T) {
	quota := NewSlidingWindowQuota(2, 60)
	if !quota.Consume(100) || !quota.Consume(120) {
		t.Fatal("first two requests should pass")
	}
	if quota.Consume(159.999) {
		t.Fatal("third request inside window should be rejected")
	}
	if !quota.Consume(160) {
		t.Fatal("oldest request should expire at exactly 60 seconds")
	}
	if got := quota.Count(160); got != 2 {
		t.Fatalf("count=%d want=2", got)
	}
}

func TestSlidingWindowQuotaClampsInvalidConfiguration(t *testing.T) {
	quota := NewSlidingWindowQuota(0, 0)
	if !quota.Consume(100) {
		t.Fatal("first request should pass")
	}
	if quota.Consume(101) {
		t.Fatal("limit should clamp to one")
	}
	if !quota.Consume(160) {
		t.Fatal("default 60-second window should expire")
	}
}
