"""Parsing a preset's `hosts` list and the text of a hostfile (ADR-033 field table)."""

from __future__ import annotations

import pytest

from crab.core.execution.hosts import Host, parse_hostfile, parse_hosts

# --- parse_hosts ---


def test_parse_hosts_localhost_without_cores() -> None:
    assert parse_hosts(["localhost"]) == (Host("localhost", None),)


def test_parse_hosts_keeps_order_and_cores() -> None:
    assert parse_hosts(["ws1:32", "ws2:16"]) == (Host("ws1", 32), Host("ws2", 16))


def test_parse_hosts_localhost_with_cores() -> None:
    assert parse_hosts(["localhost:8"]) == (Host("localhost", 8),)


def test_parse_hosts_strips_surrounding_whitespace() -> None:
    assert parse_hosts([" ws1:4 "]) == (Host("ws1", 4),)


def test_parse_hosts_empty_list() -> None:
    with pytest.raises(ValueError, match=r"^hosts: the list is empty"):
        parse_hosts([])


def test_parse_hosts_not_a_list() -> None:
    with pytest.raises(ValueError, match=r"^hosts: must be a list of strings"):
        parse_hosts("ws1")


def test_parse_hosts_non_string_entry() -> None:
    with pytest.raises(ValueError, match=r"^hosts\[0\]: must be a string"):
        parse_hosts([4])


@pytest.mark.parametrize("entry", ["", "   "])
def test_parse_hosts_empty_entry(entry: str) -> None:
    with pytest.raises(ValueError, match=r"^hosts\[1\]: .*empty"):
        parse_hosts(["ws1:4", entry])


def test_parse_hosts_name_with_inner_whitespace() -> None:
    with pytest.raises(ValueError, match=r"^hosts\[0\]: .*'ws 1'.*whitespace"):
        parse_hosts(["ws 1:4"])


@pytest.mark.parametrize("entry", ["ws1:", "ws1:x", "ws1:0", "ws1:-2"])
def test_parse_hosts_bad_core_count(entry: str) -> None:
    with pytest.raises(ValueError, match=r"^hosts\[0\]: .*positive integer"):
        parse_hosts([entry])


def test_parse_hosts_two_colons() -> None:
    with pytest.raises(ValueError, match=r"^hosts\[0\]: .*'ws1:4:5'"):
        parse_hosts(["ws1:4:5"])


def test_parse_hosts_missing_core_count_for_remote_host() -> None:
    with pytest.raises(ValueError, match=r"^hosts\[0\]: .*'ws1'.*core count"):
        parse_hosts(["ws1"])


@pytest.mark.parametrize("name", ["127.0.0.1", "LOCALHOST"])
def test_parse_hosts_only_exact_localhost_may_omit_cores(name: str) -> None:
    with pytest.raises(ValueError, match=r"core count"):
        parse_hosts([name])


def test_parse_hosts_duplicate_name() -> None:
    with pytest.raises(ValueError, match=r"^hosts\[1\]: .*duplicate.*'ws1'"):
        parse_hosts(["ws1:4", "ws1:8"])


def test_parse_hosts_where_is_the_prefix() -> None:
    with pytest.raises(ValueError, match=r"^preset 'lab': hosts: the list is empty"):
        parse_hosts([], where="preset 'lab': hosts")
    with pytest.raises(ValueError, match=r"^preset 'lab': hosts\[0\]: "):
        parse_hosts(["ws1"], where="preset 'lab': hosts")


# --- parse_hostfile ---


def test_parse_hostfile_three_forms() -> None:
    text = "localhost\nws1:32\nws2 slots=16\n"
    assert parse_hostfile(text, "hosts.txt") == (
        Host("localhost", None),
        Host("ws1", 32),
        Host("ws2", 16),
    )


def test_parse_hostfile_skips_blank_lines_and_comments() -> None:
    text = "# the lab machines\n\nws1:4\n   \n  # another\nws2:8\n"
    assert parse_hostfile(text, "hosts.txt") == (Host("ws1", 4), Host("ws2", 8))


def test_parse_hostfile_strips_trailing_comment() -> None:
    assert parse_hostfile("ws1:4  # rack A\n", "hosts.txt") == (Host("ws1", 4),)


def test_parse_hostfile_ignores_surrounding_whitespace() -> None:
    assert parse_hostfile("  \t ws1 slots=4 \t\n", "hosts.txt") == (Host("ws1", 4),)


def test_parse_hostfile_unknown_token_names_the_line() -> None:
    with pytest.raises(ValueError, match=r"^hosts\.txt line 3: .*'max_slots=4'"):
        parse_hostfile("ws1:4\n\nws1x max_slots=4\n", "hosts.txt")


@pytest.mark.parametrize("line", ["ws1 slots=0", "ws1 slots=x", "ws1 slots="])
def test_parse_hostfile_bad_slots(line: str) -> None:
    with pytest.raises(ValueError, match=r"^hosts\.txt line 2: .*positive integer"):
        parse_hostfile(f"# header\n{line}\n", "hosts.txt")


def test_parse_hostfile_both_forms_on_one_line() -> None:
    with pytest.raises(ValueError, match=r"^hosts\.txt line 1: .*both"):
        parse_hostfile("ws1:4 slots=4\n", "hosts.txt")


def test_parse_hostfile_missing_core_count_for_remote_host() -> None:
    with pytest.raises(ValueError, match=r"^hosts\.txt line 2: .*'ws1'.*core count"):
        parse_hostfile("localhost\nws1\n", "hosts.txt")


def test_parse_hostfile_bad_colon_core_count() -> None:
    with pytest.raises(ValueError, match=r"^hosts\.txt line 1: .*positive integer"):
        parse_hostfile("ws1:0\n", "hosts.txt")


def test_parse_hostfile_duplicate_host() -> None:
    with pytest.raises(ValueError, match=r"^hosts\.txt line 3: .*duplicate.*'ws1'"):
        parse_hostfile("ws1:4\nws2:4\nws1 slots=8\n", "hosts.txt")


@pytest.mark.parametrize("text", ["", "\n\n", "# only comments\n  \n"])
def test_parse_hostfile_without_hosts(text: str) -> None:
    with pytest.raises(ValueError, match=r"^hosts\.txt: no hosts"):
        parse_hostfile(text, "hosts.txt")
