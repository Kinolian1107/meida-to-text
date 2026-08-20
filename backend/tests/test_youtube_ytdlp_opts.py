from app.config import Settings
from app.pipeline.youtube import ytdlp_base_opts


def _settings(**overrides) -> Settings:
    return Settings(**overrides)


def test_pins_player_clients_away_from_android_vr():
    # Arrange
    settings = _settings()

    # Act
    opts = ytdlp_base_opts(settings)

    # Assert
    assert opts["extractor_args"]["youtube"]["player_client"] == [
        "tv_simply",
        "web_embedded",
    ]


def test_omits_player_client_when_setting_is_blank():
    # Arrange
    settings = _settings(ytdlp_player_clients="")

    # Act
    opts = ytdlp_base_opts(settings)

    # Assert
    assert "youtube" not in opts["extractor_args"]


def test_splits_and_trims_comma_separated_clients():
    # Arrange
    settings = _settings(ytdlp_player_clients=" tv_simply , , web  ")

    # Act
    opts = ytdlp_base_opts(settings)

    # Assert
    assert opts["extractor_args"]["youtube"]["player_client"] == ["tv_simply", "web"]


def test_configures_nondefault_pot_provider_port():
    # Arrange
    settings = _settings(
        ytdlp_pot_provider_url="http://127.0.0.1:14416",
    )

    # Act
    opts = ytdlp_base_opts(settings)

    # Assert
    assert opts["extractor_args"]["youtubepot-bgutilhttp"]["base_url"] == [
        "http://127.0.0.1:14416"
    ]


def test_omits_all_extractor_args_when_provider_and_clients_are_blank():
    # Arrange
    settings = _settings(
        ytdlp_player_clients="",
        ytdlp_pot_provider_url="",
    )

    # Act
    opts = ytdlp_base_opts(settings)

    # Assert
    assert "extractor_args" not in opts


def test_resolves_deno_next_to_the_running_interpreter(monkeypatch, tmp_path):
    # Arrange: a fake venv bin holding both the interpreter and deno
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python").touch()
    deno = bin_dir / "deno"
    deno.touch()
    monkeypatch.setattr("app.pipeline.youtube.sys.executable", str(bin_dir / "python"))

    # Act
    opts = ytdlp_base_opts(_settings())

    # Assert
    assert opts["js_runtimes"] == {"deno": {"path": str(deno)}}


def test_explicit_runtime_path_wins_over_auto_resolution(monkeypatch, tmp_path):
    # Arrange
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python").touch()
    (bin_dir / "deno").touch()
    monkeypatch.setattr("app.pipeline.youtube.sys.executable", str(bin_dir / "python"))
    settings = _settings(ytdlp_js_runtime_path="/opt/custom/deno")

    # Act
    opts = ytdlp_base_opts(settings)

    # Assert
    assert opts["js_runtimes"]["deno"]["path"] == "/opt/custom/deno"


def test_falls_back_to_path_lookup_when_no_bundled_deno(monkeypatch, tmp_path):
    # Arrange: interpreter with no sibling deno, but one on PATH
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python").touch()
    monkeypatch.setattr("app.pipeline.youtube.sys.executable", str(bin_dir / "python"))
    monkeypatch.setattr(
        "app.pipeline.youtube.shutil.which", lambda name: f"/usr/bin/{name}"
    )

    # Act
    opts = ytdlp_base_opts(_settings())

    # Assert
    assert opts["js_runtimes"]["deno"]["path"] == "/usr/bin/deno"


def test_carries_cookie_file_when_configured(tmp_path):
    # Arrange
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    settings = _settings(youtube_cookies_file=str(cookies))

    # Act
    opts = ytdlp_base_opts(settings)

    # Assert
    assert opts["cookiefile"] == str(cookies)
    assert opts["noplaylist"] is True


def test_probe_and_download_share_one_client_set():
    """Probing under a different client would report formats the download never sees."""
    # Arrange
    settings = _settings()

    # Act
    probe_opts = {**ytdlp_base_opts(settings), "quiet": True, "skip_download": True}
    download_opts = {**ytdlp_base_opts(settings), "quiet": False}

    # Assert
    assert probe_opts["extractor_args"] == download_opts["extractor_args"]
    assert probe_opts["js_runtimes"] == download_opts["js_runtimes"]


def test_returns_a_fresh_dict_per_call():
    # Arrange
    settings = _settings()

    # Act
    first = ytdlp_base_opts(settings)
    first["extractor_args"]["youtube"]["player_client"].append("mutated")
    second = ytdlp_base_opts(settings)

    # Assert
    assert second["extractor_args"]["youtube"]["player_client"] == [
        "tv_simply",
        "web_embedded",
    ]
