import pytest

import perfhound
from perfhound.__main__ import main


def test_package_imports():
    assert perfhound.__version__


def test_cli_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "perfhound" in capsys.readouterr().out


def test_cli_without_command_prints_help(capsys):
    assert main([]) == 0
    assert "candidates" in capsys.readouterr().out
