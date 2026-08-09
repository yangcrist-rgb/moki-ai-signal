# Strategy contract

A strategy implements `id`, `version`, `default_parameters()`, and `run(frame, parameters)`.

Return a `StrategyResult` containing confirmed `SignalEvent` rows, the current direction, and an optional pending signal. Events must retain both pivot and confirmation indices. The renderer only consumes this normalized result; do not make a renderer depend on a strategy's private state.

Register each strategy in `scripts/moki_signal/strategy.py`. Adding a strategy must not change `s1-reconstruction@1.0.0` or `moki-ai-signal-v1`.
