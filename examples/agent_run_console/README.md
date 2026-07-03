# Agent Run Console

This example renders a local, self-contained HTML view from HydraMind
observation events. It is an operator/demo surface, not a framework-core
dashboard.

Typical use from tests or smoke scripts:

```python
from examples.agent_run_console.render import write_console

write_console(events, "artifacts/run-console.html")
```

The generated page shows session status, node executions, model/tool events,
and gate/control transitions from trace artifacts. It does not read secrets or
mutate `RuntimeSession`.
