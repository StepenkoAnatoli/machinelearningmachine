"""
Saved transcripts: the write is atomic, the listing is cheap, trimming is stated.

Three defects this file closes:

* ``path.write_text(...)`` truncated the file before filling it in, so a crash, an
  interrupted shutdown or a full disk left a half-written transcript that
  ``list_sessions`` skipped *silently* - indistinguishable from "never saved".
* Listing read and parsed every stored transcript to work out how many messages
  each had: 50 sessions of 1.5 MB cost 154 ms per ``GET /api/sessions`` (measured),
  and the size cap allows 8 MB each.
* When a session was trimmed to fit that cap, nothing said so - the file just
  quietly started mid-conversation.
"""

import json
import os
import time

import pytest

from machinelearningmachine import sessions as store


def _messages(n, size=40):
    return [
        {
            "id": f"m{i}",
            "sender_id": "copilot",
            "sender_name": "GitHub Copilot",
            "recipient_id": "*",
            "topic": "general",
            "message_type": "proposal",
            "content": ("answer " + "x" * size)[: size + 20],
            "artifacts": {},
            "metadata": {"simulated": True},
            "timestamp": 1_700_000_000 + i,
        }
        for i in range(n)
    ]


AGENTS = [{"agent_id": "copilot", "name": "GitHub Copilot", "role": "Code", "system_prompt": "…"}]


# ---------------------------------------------------------------- atomic writes

def test_a_failed_save_leaves_the_previous_version_intact(monkeypatch, tmp_path):
    meta = store.save_session("important", AGENTS, _messages(4))
    path = store._path_for(meta["id"])
    before = path.read_text(encoding="utf-8")

    def exploding_replace(src, dst):
        os.unlink(src)          # the temp file is cleaned up...
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(store.os, "replace", exploding_replace)
    with pytest.raises(OSError) as exc:
        store.save_session("important", AGENTS, _messages(9))
    assert "could not be written" in str(exc.value)
    assert "No space left" in str(exc.value)

    # The already-saved transcript is byte-identical, not half-written.
    assert path.read_text(encoding="utf-8") == before
    assert json.loads(before)["message_count"] == 4


def test_the_reason_for_a_failed_save_survives_to_the_caller(monkeypatch, tmp_path):
    """"Could not save the session. Please try again." tells the operator nothing."""
    store.save_session("keep", AGENTS, _messages(2))

    def exploding_replace(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(store.os, "replace", exploding_replace)
    with pytest.raises(OSError) as exc:
        store.save_session("keep", AGENTS, _messages(3))
    assert exc.value.reason_detail == "No space left on device"


def test_no_temporary_litter_survives_a_failed_write(monkeypatch):
    def exploding_replace(src, dst):
        raise OSError(5, "I/O error")

    monkeypatch.setattr(store.os, "replace", exploding_replace)
    with pytest.raises(OSError):
        store.save_session("nope", AGENTS, _messages(2))
    assert list(store.sessions_dir().glob("*.tmp")) == []


def test_stale_temp_files_are_collected_by_the_prune(tmp_path):
    directory = store.sessions_dir()
    stale = directory / "20200101-000000-abc123.json.999.tmp"
    fresh = directory / "20200101-000001-def456.json.999.tmp"
    stale.write_text("{", encoding="utf-8")
    fresh.write_text("{", encoding="utf-8")
    old = time.time() - 7200
    os.utime(stale, (old, old))

    store._prune_old_sessions(now=time.time())
    assert stale.exists() is False, "an abandoned temp file must not pile up"
    assert fresh.exists(), "a save in progress must not be deleted"

    store._prune_old_sessions(now=time.time())
    assert fresh.exists()


# ------------------------------------------------------------------ the listing

def test_listing_reads_only_the_header(tmp_path, monkeypatch):
    store.save_session("one", AGENTS, _messages(200, size=2000))
    store.save_session("two", AGENTS, _messages(120, size=2000))

    directory = store.sessions_dir()
    sizes = []
    real_open = open

    class Reader:
        def __init__(self, file, *a, **kw):
            self._fh = real_open(file, *a, **kw)
            self._tracked = str(file).startswith(str(directory))

        def __getattr__(self, name):
            return getattr(self._fh, name)

        def read(self, *a, **kw):
            out = self._fh.read(*a, **kw)
            if self._tracked:
                sizes.append(len(out))
            return out

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return self._fh.__exit__(*exc)

    monkeypatch.setattr("builtins.open", Reader)
    listed = store.list_sessions()
    monkeypatch.undo()

    assert {row["name"] for row in listed} == {"one", "two"}
    counts = {row["name"]: row["message_count"] for row in listed}
    assert counts == {"one": 200, "two": 120}, "message_count comes from the header, not a recount"
    assert sizes, "the instrumented reads should have happened"
    assert max(sizes) <= store.META_HEAD_BYTES, f"a listing read {max(sizes)} bytes from one file"
    assert sum(sizes) < 200_000, "the whole listing must stay in kilobytes, not megabytes"


def test_listing_uses_the_stored_count_not_the_body(tmp_path):
    """A body that disagrees with the header is a corrupt file, not a question."""
    meta = store.save_session("row", AGENTS, _messages(7))
    path = store._path_for(meta["id"])
    document = json.loads(path.read_text(encoding="utf-8"))
    document["messages"] = document["messages"][:2]      # truncated behind the header's back
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    listed = store.list_sessions()
    assert listed[0]["message_count"] == 7
    loaded = store.get_session(meta["id"])
    assert loaded is not None and len(loaded["messages"]) == 2


def test_a_foreign_document_still_lists_through_the_slow_path(tmp_path):
    directory = store.sessions_dir()
    (directory / "20240101-000000-hand.json").write_text(
        json.dumps({"messages": [1, 2, 3], "name": "hand made", "id": "20240101-000000-hand"}),
        encoding="utf-8",
    )
    listed = store.list_sessions()
    assert [s["name"] for s in listed] == ["hand made"]
    assert listed[0]["message_count"] == 3


def test_corrupt_files_are_skipped_not_fatal(tmp_path):
    store.save_session("good", AGENTS, _messages(3))
    directory = store.sessions_dir()
    (directory / "20240101-000000-bad.json").write_text('{"id": "trunc', encoding="utf-8")
    listed = store.list_sessions()
    assert [s["name"] for s in listed] == ["good"]


# ------------------------------------------------------------------- trimming

def test_an_oversized_session_is_trimmed_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "MAX_SESSION_BYTES", 4000)
    big = _messages(60, size=400)
    meta = store.save_session("huge", AGENTS, big)

    document = store.get_session(meta["id"])
    assert document is not None
    assert len(document["messages"]) < len(big)
    assert document["trimmed"] is True
    assert document["messages_dropped_on_save"] > 0
    assert document["message_count"] == len(document["messages"])
    # The trimmed document itself must fit the cap it was trimmed for.
    assert len(json.dumps(document, ensure_ascii=False).encode("utf-8")) <= 6000

    listed = store.list_sessions()
    assert listed[0]["trimmed"] is True, "the list row must not imply a full transcript"


def test_large_trimmed_session_stores_trimmed_flag_in_header_and_fast_path_reads_it(tmp_path):
    """
    D30: when a session with 1000+ messages exceeds MAX_SESSION_BYTES and is trimmed,
    the 'trimmed' flag must live in the leading header so _head_meta can see it without
    reading past META_HEAD_BYTES, and save_session must return trimmed=True.
    """
    big = _messages(1000, size=10_000)
    meta = store.save_session("large-trimmed", AGENTS, big)
    assert meta.get("trimmed") is True, "save_session must return trimmed=True for trimmed session"

    path = store._path_for(meta["id"])
    assert path.stat().st_size > store.META_HEAD_BYTES, "test requires a file larger than META_HEAD_BYTES"

    # _head_meta only reads META_HEAD_BYTES:
    head = store._head_meta(path)
    assert head is not None, "fast path must match"
    assert head.get("trimmed") is True, "fast path must read trimmed=True from header without full parse"

    listed = store.list_sessions()
    assert listed[0]["id"] == meta["id"]
    assert listed[0].get("trimmed") is True, "list_sessions must report trimmed=True from header"


def test_the_kept_messages_are_the_most_recent_ones(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "MAX_SESSION_BYTES", 4000)
    messages = _messages(60, size=400)
    last_id = messages[-1]["id"]
    meta = store.save_session("huge", AGENTS, messages)
    document = store.get_session(meta["id"])
    assert document["messages"][-1]["id"] == last_id


def test_a_single_huge_message_does_not_loop_forever(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "MAX_SESSION_BYTES", 1000)
    meta = store.save_session("one huge", AGENTS, _messages(1, size=900_000))
    document = store.get_session(meta["id"])
    assert document is not None
    assert document["message_count"] in (0, 1)


# --------------------------------------------------------------------- deleting

def test_delete_removes_only_the_named_session(tmp_path):
    a = store.save_session("a", AGENTS, _messages(1))
    b = store.save_session("b", AGENTS, _messages(1))
    assert store.delete_session(a["id"]) is True
    assert store.get_session(a["id"]) is None
    assert store.get_session(b["id"]) is not None
    assert store.delete_session("../../../etc/passwd") is False
    assert store.delete_session("nope-not-here") is False


def test_prune_order_uses_the_timestamp_in_the_name_not_the_random_suffix():
    """
    Sorting by mtime alone was wrong twice over: several saves inside one second
    share a timestamp on a coarse filesystem, and the tie-breaker was the random
    suffix - so the newest session could be the one deleted.
    """
    same_second_a = store._prune_sort_key("20260919-101010-zzzzzz", 1_000.0)
    same_second_b = store._prune_sort_key("20260919-101010-aaaaaa", 1_000.0)
    assert sorted([same_second_a, same_second_b], reverse=True)[0] == same_second_a == same_second_b[:15]

    older = store._prune_sort_key("20260919-090909-zzzzzz", 900.0)
    newer = store._prune_sort_key("20260919-101010-aaaaaa", 900.0)
    assert newer > older, "the embedded timestamp decides, even when mtime says otherwise"

    # A hand-written file with no timestamp in its name is ordered by mtime.
    foreign = store._prune_sort_key("notes-from-last-week", 1_700_000_000.0)
    assert foreign == time.strftime("%Y%m%d-%H%M%S", time.localtime(1_700_000_000.0))


def test_pruning_keeps_the_newest_session_even_within_one_second(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "MAX_SESSIONS", 2)
    directory = store.sessions_dir()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    ids = []
    for suffix in ("aaaaaa", "bbbbbb", "cccccc"):
        session_id = f"{stamp}-{suffix}"
        (directory / f"{session_id}.json").write_text(
            json.dumps({"id": session_id, "name": session_id, "created_at": 1, "messages": []}),
            encoding="utf-8",
        )
        os.utime(directory / f"{session_id}.json", (1_000.0, 1_000.0))   # identical mtimes
        ids.append(session_id)

    store._prune_old_sessions()
    remaining = sorted(p.stem for p in directory.glob("*.json"))
    assert remaining == sorted(ids[1:]), "the just-saved session must survive, the oldest must go"
