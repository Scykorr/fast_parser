from fast_parser.cli import main

from conftest import settings_for


def test_run_logs_the_sentiment_hint_when_nothing_is_labelled(tmp_path, capsys):
    settings_for(tmp_path, "sources: []\n")
    assert main(["--config", str(tmp_path / "settings.yaml"), "run"]) == 0
    captured = capsys.readouterr()
    assert "Прогон завершён" in captured.out
    assert (
        'Тональность не посчитана: установите pip install -e ".[ml]" и выполните fast-parser process'
        in captured.err
    )


def test_stats_run_and_export_on_empty_catalog(tmp_path, capsys):
    settings_for(tmp_path, "sources: []\n")
    config = str(tmp_path / "settings.yaml")
    assert main(["--config", config, "stats"]) == 0
    stats_out = capsys.readouterr().out
    assert "Источники: 0" in stats_out
    assert "Статьи: 0" in stats_out

    assert main(["--config", config, "run"]) == 0
    run_out = capsys.readouterr().out
    assert "Прогон завершён" in run_out
    assert "новых 0" in run_out

    out = tmp_path / "articles.jsonl"
    assert main(["--config", config, "export", "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == ""


def test_missing_catalog_is_a_usage_error(tmp_path, capsys):
    config = tmp_path / "settings.yaml"
    config.write_text("sources_file: missing.yaml\n", encoding="utf-8")
    assert main(["--config", str(config), "stats"]) == 2
    assert "не найден" in capsys.readouterr().err


def test_loop_interval_must_be_positive(tmp_path, capsys):
    settings_for(tmp_path, "sources: []\n")
    code = main(["--config", str(tmp_path / "settings.yaml"), "loop", "--interval", "0"])
    assert code == 2
    assert "Интервал" in capsys.readouterr().err
