import perfhound
from perfhound.__main__ import main


def test_package_imports():
    assert perfhound.__version__


def test_cli_version(capsys):
    assert main(["--version"]) == 0
    assert "perfhound" in capsys.readouterr().out
