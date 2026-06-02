"""Behavior of the idempotent seeder (module E).

The USER layer under ``/mnt/data/wb-docker-apps/<app>/`` is seed-if-absent
(design.md §3.6): files and dirs are created only when missing, and a user's
edits must survive byte-for-byte across re-runs. These tests exercise that on
a real filesystem under ``tmp_path``.
"""

from wb_docker_app.seeding import seed_app_dir


def test_seeds_files_and_dirs_when_absent_and_reports_them(tmp_path):
    app_root = tmp_path / "node-red"

    created = seed_app_dir(
        app_root,
        files={"docker-compose.override.yml": "services: {}\n", ".env": "TZ=UTC\n"},
        dirs=["data"],
    )

    assert (app_root / "docker-compose.override.yml").read_text() == "services: {}\n"
    assert (app_root / ".env").read_text() == "TZ=UTC\n"
    assert (app_root / "data").is_dir()
    assert sorted(created) == sorted(
        ["docker-compose.override.yml", ".env", "data"]
    )


def test_preexisting_file_is_left_untouched_and_not_reported(tmp_path):
    app_root = tmp_path / "node-red"
    app_root.mkdir()
    edited = app_root / ".env"
    edited.write_text("TZ=Europe/Moscow  # hand-edited\n")

    created = seed_app_dir(app_root, files={".env": "TZ=UTC\n"})

    assert edited.read_text() == "TZ=Europe/Moscow  # hand-edited\n"
    assert created == []


def test_second_identical_call_is_a_no_op(tmp_path):
    app_root = tmp_path / "node-red"
    files = {"docker-compose.override.yml": "services: {}\n"}

    first = seed_app_dir(app_root, files=files, dirs=["data"])
    second = seed_app_dir(app_root, files=files, dirs=["data"])

    assert first  # first call did seed something
    assert second == []


def test_nested_relative_file_path_is_created_with_parents(tmp_path):
    app_root = tmp_path / "node-red"

    created = seed_app_dir(
        app_root, files={"data/settings/config.json": "{}\n"}
    )

    assert (app_root / "data" / "settings" / "config.json").read_text() == "{}\n"
    assert created == ["data/settings/config.json"]
