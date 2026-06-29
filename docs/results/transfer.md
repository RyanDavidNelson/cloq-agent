# Held-out transferability study

First held-out generalization number for cloq-agent (the figure docs/RESULTS.md flagged
as missing). 20 functions reduced from pinned OpenSSL 3.4.0 + FreeRTOS-Kernel V11.1.0
(see eval/transfer/PINNED.md); the target's gold invariant/proof is withheld from the
proof library and few-shot, so a pass is generalization, not recall. Straight-line
targets are machine-checked to **Qed** with a CFG-derived deterministic proof (no LLM,
no recall); ceiling classes are reported at their named wall (the documented limitation),
not attempted via synthesis.

## Held-out success by suite x tier (proved / total)

| suite | easy | medium | hard | suite total |
|---|---|---|---|---|
| openssl | 4/5 | 0/3 | 0/2 | 4/10 |
| freertos | 4/5 | 0/3 | 0/2 | 4/10 |
| **both** | 8/10 | 0/6 | 0/4 | **8/20** |

## Per-target

| suite | tier | target | property | result | ceiling/cluster | mode | iters | wall(s) |
|---|---|---|---|---|---|---|---|---|
| openssl | easy | value_barrier | ct | not proved | degenerate (identity / 0-cycle body) | deterministic | 10 | 0.7 |
| openssl | easy | constant_time_msb | ct | PROVED | - | deterministic | 10 | 0.7 |
| openssl | easy | constant_time_is_zero | ct | PROVED | - | deterministic | 10 | 1.0 |
| openssl | easy | constant_time_eq | ct | PROVED | - | deterministic | 10 | 1.2 |
| openssl | easy | constant_time_select | ct | PROVED | - | deterministic | 10 | 0.9 |
| openssl | medium | CRYPTO_memcmp | ct | ceiling: array/pointer loop | array/pointer | n/a | 0 | 0.1 |
| openssl | medium | OPENSSL_cleanse | wcet | ceiling: array/pointer loop | array/pointer | n/a | 0 | 0.1 |
| openssl | medium | ChaCha20_block | wcet | reduction pending | lift-gap | n/a | 0 | 0.0 |
| openssl | hard | BN_consttime_swap | ct | ceiling: array/pointer loop | array/pointer | n/a | 0 | 0.3 |
| openssl | hard | Poly1305_block | wcet | reduction pending | lift-gap | n/a | 0 | 0.0 |
| freertos | easy | vListInitialise | wcet | PROVED | - | deterministic | 10 | 1.9 |
| freertos | easy | vListInitialiseItem | wcet | PROVED | - | deterministic | 10 | 0.6 |
| freertos | easy | vListInsertEnd | wcet | PROVED | - | deterministic | 10 | 3.2 |
| freertos | easy | xTaskGetCurrentTaskHandle | wcet | PROVED | - | deterministic | 10 | 0.7 |
| freertos | easy | prvResetNextTaskUnblockTime | wcet | reduction pending | lift-gap | n/a | 0 | 0.0 |
| freertos | medium | uxListRemove | wcet | not proved | straight-line memory-reasoning gap | deterministic | 10 | 4.5 |
| freertos | medium | xTaskResumeAll | wcet | reduction pending | lift-gap | n/a | 0 | 0.0 |
| freertos | medium | uxQueueMessagesWaiting | wcet | reduction pending | lift-gap | n/a | 0 | 0.0 |
| freertos | hard | vListInsert | wcet | ceiling: unsupported control flow | unsupported-cfg | n/a | 0 | 0.2 |
| freertos | hard | xTaskIncrementTick | wcet | reduction pending | lift-gap | n/a | 0 | 0.0 |

## Failure clusters (mapped to the capability matrix)

- **array/pointer** (3): openssl/CRYPTO_memcmp, openssl/OPENSSL_cleanse, openssl/BN_consttime_swap
- **degenerate (identity / 0-cycle body)** (1): openssl/value_barrier
- **lift-gap** (6): openssl/ChaCha20_block, openssl/Poly1305_block, freertos/prvResetNextTaskUnblockTime, freertos/xTaskResumeAll, freertos/uxQueueMessagesWaiting, freertos/xTaskIncrementTick
- **straight-line memory-reasoning gap** (1): freertos/uxListRemove
- **unsupported-cfg** (1): freertos/vListInsert

### Reading the result

Easy = the pipeline's sweet spot (branchless straight-line) and the source of the real
held-out pass rate. Medium/hard deliberately probe the wall and land at a named ceiling
class (array/pointer, search/early-exit, memory-aliasing, unsupported-CFG) or a
straight-line-with-memory proof gap — that distribution is the transfer finding. Targets
marked *reduction pending* drag in too much build machinery (full FreeRTOSConfig / a
configured OpenSSL tree) and are recorded as lift gaps, per the candidate-swap note.

