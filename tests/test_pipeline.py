import pytest

from desk.pipeline import DATASETS, parse_datasets


def test_no_arguments_selects_every_dataset():
    assert parse_datasets([]) == DATASETS


def test_named_dataset_is_selected():
    assert parse_datasets(["rtfo"]) == ["rtfo"]


def test_unknown_dataset_is_rejected():
    with pytest.raises(SystemExit):
        parse_datasets(["eua"])
