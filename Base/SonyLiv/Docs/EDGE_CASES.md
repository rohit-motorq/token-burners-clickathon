# Edge Case Handling — Foreground-Only Concurrency
## Token Burners · Click-a-thon 2026

---

## Summary

| Category | Edge Cases | Count in Data | Risk if Missed |
|----------|-----------|---------------|----------------|
| Duplicate Transitions | 5 cases | 25,768 events | 🔴 Concurrency goes negative or spikes |
| Terminal State | 4 cases | 538 events | 🔴 Dead sessions resurrect |
| Session Lifecycle | 4 cases | 42 sessions | 🟡 Minor overcounting |
| Foreground/Background | 5 cases | 11,762 events | 🔴 Massive overcounting |
| Timeout/Heartbeat | 4 cases | All sessions | 🔴 Never-ending sessions |
| User/Session Identity | 4 cases | 506 sessions | 🟡 User-level inaccuracy |
| Timestamp/Ordering | 4 cases | 894 events | 🟡 Race conditions |
| Content/Dimensions | 3 cases | ~2,250 events | 🟢 Filter inaccuracy |

**Total: 33 distinct edge cases. 3 are non-negotiable (P0).**

---

## NON-NEGOTIABLE: The 3 Rules That Cannot Be Broken

### Rule 1: Only emit delta when state CHANGES
