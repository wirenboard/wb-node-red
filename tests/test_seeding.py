"""Behavior of the idempotent seeder (module E).

The USER layer under ``/mnt/data/wb-docker-apps/<app>/`` is seed-if-absent
(design.md §3.6): files and dirs are created only when missing, and a user's
edits must survive byte-for-byte across re-runs. These tests exercise that on
a real filesystem under ``tmp_path``.
"""

from wb_docker_app.seeding import refresh_tree, seed_app_dir, seed_tree


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


# --- seed_tree: a package's per-app default-config tree ----------------------


def test_seed_tree_copies_a_nested_file_when_absent(tmp_path):
    src = tmp_path / "seed"
    (src / "data").mkdir(parents=True)
    (src / "data" / "flows.json").write_text("[]\n")
    dst = tmp_path / "node-red"

    created = seed_tree(src, dst)

    assert (dst / "data" / "flows.json").read_text() == "[]\n"
    assert created == ["data/flows.json"]


def test_seed_tree_does_not_overwrite_an_existing_destination(tmp_path):
    src = tmp_path / "seed"
    (src / "data").mkdir(parents=True)
    (src / "data" / "flows.json").write_text("[]\n")
    dst = tmp_path / "node-red"
    (dst / "data").mkdir(parents=True)
    sentinel = dst / "data" / "flows.json"
    sentinel.write_text('[{"id": "user-edit"}]\n')

    created = seed_tree(src, dst)

    assert sentinel.read_text() == '[{"id": "user-edit"}]\n'  # user edit survives
    assert created == []


def test_seed_tree_returns_empty_when_src_dir_is_missing(tmp_path):
    src = tmp_path / "does-not-exist"
    dst = tmp_path / "node-red"

    assert seed_tree(src, dst) == []


def test_seed_tree_is_idempotent_on_re_run(tmp_path):
    src = tmp_path / "seed"
    (src / "data").mkdir(parents=True)
    (src / "data" / "flows.json").write_text("[]\n")
    dst = tmp_path / "node-red"

    first = seed_tree(src, dst)
    second = seed_tree(src, dst)

    assert first == ["data/flows.json"]
    assert second == []


# --- refresh_tree: a package's OWNED code (the vendored palette) -------------


def test_refresh_tree_copies_a_nested_file_when_absent(tmp_path):
    src = tmp_path / "palette"
    (src / "node-red-contrib-wirenboard").mkdir(parents=True)
    (src / "node-red-contrib-wirenboard" / "index.js").write_text("// v1\n")
    dst = tmp_path / "node_modules"

    written = refresh_tree(src, dst)

    assert (dst / "node-red-contrib-wirenboard" / "index.js").read_text() == "// v1\n"
    assert written == ["node-red-contrib-wirenboard/index.js"]


def test_refresh_tree_overwrites_an_existing_destination(tmp_path):
    # Unlike seed_tree, the palette is PACKAGE-OWNED code: a newer .deb must
    # overwrite the previously delivered subtree (docs/adr/0006, delivery b).
    src = tmp_path / "palette"
    (src / "node-red-contrib-wirenboard").mkdir(parents=True)
    (src / "node-red-contrib-wirenboard" / "index.js").write_text("// v2 NEW\n")
    dst = tmp_path / "node_modules"
    (dst / "node-red-contrib-wirenboard").mkdir(parents=True)
    (dst / "node-red-contrib-wirenboard" / "index.js").write_text("// v1 OLD\n")

    written = refresh_tree(src, dst)

    assert (dst / "node-red-contrib-wirenboard" / "index.js").read_text() == "// v2 NEW\n"
    assert written == ["node-red-contrib-wirenboard/index.js"]


def test_refresh_tree_leaves_unrelated_files_untouched(tmp_path):
    # A user-installed palette sharing /data/node_modules must survive: refresh
    # only touches paths present in the package's vendored subtree.
    src = tmp_path / "palette"
    (src / "node-red-contrib-wirenboard").mkdir(parents=True)
    (src / "node-red-contrib-wirenboard" / "index.js").write_text("// wb\n")
    dst = tmp_path / "node_modules"
    user_pkg = dst / "node-red-contrib-user-thing"
    user_pkg.mkdir(parents=True)
    (user_pkg / "index.js").write_text("// user installed\n")

    refresh_tree(src, dst)

    assert (user_pkg / "index.js").read_text() == "// user installed\n"


def test_refresh_tree_returns_empty_when_src_dir_is_missing(tmp_path):
    # A service that ships no palette (e.g. a future host-mode service) just
    # delivers nothing.
    src = tmp_path / "does-not-exist"
    dst = tmp_path / "node_modules"

    assert refresh_tree(src, dst) == []
