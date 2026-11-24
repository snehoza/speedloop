# Cache registry

Every cache the code keeps, one row each. `speedloop registry check` fails when the code has a cache
that is not listed here, or this table lists one the code no longer has.

Keep rule: a cache stays at >= 1 s saved per 100 MiB held, goes below 0.5 s, and a person decides in
between (`speedloop keep SECONDS MIB`).

| name | holds | key / invalidated by | size (MiB) | saves (s) | s per 100 MiB | decision |
| --- | --- | --- | ---: | ---: | ---: | --- |
