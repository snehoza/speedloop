# Speed round <N>: <one-line goal>

**Verdict:** <adopt / partial / reject, in one sentence>
**Commit(s):** <sha> · **Base:** <sha> · **Date:** <yyyy-mm-dd>
**Machine:** <CPU, cores, OS> · **CPU counts measured:** <e.g. 4 and 16> · **Lock held, machine quiet:** <yes/no>

## Results

Interleaved A/A + ABBA (`speedloop bench --pairs 2 --aa-pairs 1`), medians. A delta counts only beyond
the noise with every run of one side beyond every run of the other.

| target | CPUs | A/A spread | base | candidate | delta | verdict |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| | | | | | | |

Per-step breakdown: `<ledger.md>` (`speedloop ledger`).

## Exactness

`speedloop golden check` (tolerance: adopt <= <x>, ask <= <y>, <absolute|relative>):
<IDENTICAL on all N goldens | adopt: X values changed in Y files, max |diff| <d> |
ask: review files in results-.../review, approved by <name> on <date>>

## Memory

| target | base peak MiB | candidate peak MiB | delta | verdict | new caches (s saved per 100 MiB) |
| --- | ---: | ---: | ---: | --- | --- |

## Changes in this round

1. <change> - <why it is faster> - <proof: exact / microbench numbers>

## Not done / new ideas

Moved to `SPEED_QUEUE.md`: <list>
