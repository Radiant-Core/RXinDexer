"""Shared test helpers.

A normal importable module rather than a conftest so both ``tests/`` and
``tests/server/`` can import the same objects by a stable path
(``from tests.support import FakeEnv``). ``pytest.ini`` puts the project root
on ``sys.path`` via ``pythonpath = ..``, so that path resolves the same way
regardless of which directory a test lives in.
"""


class FakeEnv:
    """Minimal stand-in for ``electrumx.server.env.Env`` for index constructors.

    Deliberately a plain object rather than a ``Mock``. The index classes read
    optional settings with ``getattr(env, name, default)``, and a bare ``Mock``
    auto-creates *every* attribute as a truthy ``Mock`` — so the default never
    applies and the code receives a ``Mock`` where it expected a set, int or
    bool. That is what broke ``GlyphIndex.__init__``, which does::

        raw_denylist = getattr(env, 'dmint_denylist', set())
        if raw_denylist:
            for ref_str in raw_denylist:

    Correct against a real ``Env`` (``dmint_denylist`` is always a set, empty
    by default), but against a fixture that never mentioned the setting it
    raised ``TypeError: 'Mock' object is not iterable`` — the fixture was
    silently promising a denylist the real Env would have left empty.

    Here, attributes that were not passed genuinely do not exist, so
    ``getattr`` falls back to the production default exactly as against a real
    Env. A newly added optional setting therefore cannot retroactively break
    these fixtures: the test env behaves like an Env where the operator set
    nothing.

    Defaults mirror ``Env``: glyph indexing on, empty denylist. ``reorg_limit``
    is 10 rather than the coin default purely to keep test undo-window maths
    small and readable. Pass keywords to override, or to add an attribute a
    specific test needs::

        FakeEnv()                                   # usable default
        FakeEnv(glyph_index=False)                  # indexing disabled
        FakeEnv(dmint_denylist={'ab' * 32 + '_0'})  # exercise the denylist path
    """

    def __init__(self, **overrides):
        self.glyph_index = True
        self.reorg_limit = 10
        self.dmint_denylist = set()
        for key, value in overrides.items():
            setattr(self, key, value)

    def __repr__(self):
        return f'FakeEnv({self.__dict__!r})'
