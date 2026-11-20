"""A minimal backend adapter for tests only (1.0 readiness 067). It is never in agents.ADAPTERS
on its own: a test registers it in its own process with agents.register() and removes it after,
so the command line (`pai run --agent ...`, `pai doctor`) can never select it.

It deliberately lacks what a supported backend has: no Remote Control mode, readiness from the
screen only, and no transcript, so a dispatched task could never be shown delivered."""
import agents


class Echo(agents.Adapter):
    name, display, program = "echo-fake", "Echo (test fake)", "echo-fake"
    modes = ("local",)
    trust_markers = ("trust this echo folder?",)
    trust_hint = "answer it yourself"
    ready_markers = ("echo> ",)
    readiness = "screen"
    transcript = None
    no_rc_reason = "the test fake has no Remote Control"

    def argv(self, program, mode, name, model, effort=None):
        return list(program)


def registered():
    """Context manager: Echo is selectable inside the with-block of this process only."""
    import contextlib

    @contextlib.contextmanager
    def cm():
        agents.register(Echo())
        try:
            yield agents.get("echo-fake")
        finally:
            agents.unregister("echo-fake")
    return cm()
