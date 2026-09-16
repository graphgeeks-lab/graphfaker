"""The PyTorch Geometric export: features, edge indices, labels, splits.

The array tests need only numpy; the HeteroData tests skip when PyG is not
installed (``pip install "graphfaker[pyg]"``).
"""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from graphfaker.backends.tables import ID, SOURCE, TARGET
from graphfaker.domains import fraud, social
from graphfaker.engine import generate
from graphfaker.sinks.pyg import arrays, encode_features


@pytest.fixture(scope="module")
def run():
    return fraud.generate(scale=0.0005, seed=11, hardness="medium")


@pytest.fixture(scope="module")
def built(run):
    return arrays(run.tables, run.truth, seed=3)


# ---------------------------------------------------------------- features


def test_feature_encoding_rules():
    frame = pl.DataFrame(
        {
            "id": ["a", "b", "c", "d"],
            "n": [1.0, 2.0, 3.0, 4.0],
            "flag": [True, False, True, None],
            "when": pl.Series([1, 2, 3, 4]).cast(pl.Date),
            "kind": ["x", "y", "x", "y"],
            "email": ["a@x", "b@x", "c@x", "d@x"],
        }
    )
    x, names, stats = encode_features(frame, exclude={"id"})
    assert names == ["n", "flag", "when", "kind=x", "kind=y"]
    assert x.dtype == np.float32 and x.shape == (4, 5)
    assert abs(float(x[:, 0].mean())) < 1e-6 and abs(float(x[:, 0].std()) - 1) < 1e-6, "numeric columns are standardised"
    assert stats["n"][0] == 2.5
    assert x[:, 1].tolist() == [1.0, 0.0, 1.0, 0.0], "a null boolean is false"
    assert x[:, 3].tolist() == [1.0, 0.0, 1.0, 0.0]

    x, names, _ = encode_features(frame, exclude={"id"}, standardize=False)
    assert x[:, 0].tolist() == [1.0, 2.0, 3.0, 4.0]
    assert x[:, 2].tolist() == [0.0, 1.0, 2.0, 3.0], "dates become days since the earliest"


def test_a_table_with_nothing_usable_gets_a_constant_feature():
    frame = pl.DataFrame({"id": ["a", "b"], "name": ["Ann", "Bob"]})
    x, names, _ = encode_features(frame, exclude={"id"})
    assert names == ["constant"] and x.shape == (2, 1)


def test_ids_foreign_keys_and_latent_factors_stay_out_of_x(built):
    for node in built.nodes.values():
        assert ID not in node.feature_names
        assert not any(name.startswith("region") for name in node.feature_names)
        assert "region" in node.latent
    assert "customer" not in built.nodes["Account"].feature_names, "Account.customer is the OWNS edge, not a feature"
    assert "balance" in built.nodes["Account"].feature_names
    assert any(name.startswith("account_type=") for name in built.nodes["Account"].feature_names)


# -------------------------------------------------------------------- edges


def test_edge_index_points_at_the_right_rows(run, built):
    key = ("Account", "TRANSFERS", "Account")
    edge = built.edges[key]
    frame = run.tables.edges["TRANSFERS"]
    ids = built.nodes["Account"].ids
    assert edge.edge_index.shape == (2, frame.height) and edge.edge_index.dtype == np.int64
    assert ids[edge.edge_index[0]].tolist() == frame[SOURCE].to_list()
    assert ids[edge.edge_index[1]].tolist() == frame[TARGET].to_list()
    assert edge.edge_time is not None and edge.edge_time.dtype == np.int64
    assert "amount" in edge.feature_names and "tx_id" not in edge.feature_names

    owns = built.edges[("Customer", "OWNS", "Account")]
    assert owns.edge_index[0].max() < len(built.nodes["Customer"].ids)


# ------------------------------------------------------------------- labels


def test_labels_match_the_truth(run, built):
    account = built.nodes["Account"]
    members = run.truth["accounts"]
    assert int(account.y.sum()) == members.filter(pl.col("is_fraud"))["account_id"].n_unique()
    decoy_only = set(members.filter(~pl.col("is_fraud"))["account_id"]) - set(members.filter(pl.col("is_fraud"))["account_id"])
    assert int(account.decoy.sum()) == len(decoy_only)
    assert not np.any(account.y & account.decoy), "a decoy is not fraud"

    transfers = built.edges[("Account", "TRANSFERS", "Account")]
    expected = run.truth["transactions"].filter(pl.col("is_fraud")).join(run.tables.edges["TRANSFERS"], on="tx_id").height
    assert int(transfers.y.sum()) == expected
    assert built.edges[("Customer", "OWNS", "Account")].y is None, "only transactions carry edge labels"


def test_splits_are_stratified_and_disjoint(built):
    account = built.nodes["Account"]
    masks = account.masks
    total = masks["train_mask"].astype(int) + masks["val_mask"].astype(int) + masks["test_mask"].astype(int)
    assert total.tolist() == [1] * len(account.ids), "every node is in exactly one split"
    positives = int(account.y.sum())
    for name, share in zip(("train_mask", "val_mask", "test_mask"), (0.6, 0.2, 0.2)):
        assert abs(int(account.y[masks[name]].sum()) - positives * share) <= 1


def test_splits_are_reproducible_by_seed(run):
    a = arrays(run.tables, run.truth, seed=5).nodes["Account"].masks["train_mask"]
    b = arrays(run.tables, run.truth, seed=5).nodes["Account"].masks["train_mask"]
    c = arrays(run.tables, run.truth, seed=6).nodes["Account"].masks["train_mask"]
    assert a.tolist() == b.tolist() and a.tolist() != c.tolist()


def test_without_truth_there_are_no_labels(run):
    blind = arrays(run.tables)
    assert blind.nodes["Account"].y is None and blind.nodes["Account"].masks == {}
    assert blind.edges[("Account", "TRANSFERS", "Account")].y is None
    assert "region" in blind.nodes["Account"].feature_names, "without the truth nothing says region is latent"


def test_social_graph_exports_with_community_as_a_label():
    run = generate(social.schema(80, 300), seed=1)
    built = arrays(run.tables, run.truth)
    person = built.nodes["Person"]
    assert "community" in person.latent and person.y is None
    assert not any(name.startswith("name=") for name in person.feature_names), "names are identifiers, not categories"
    assert ("Person", "FRIENDS_WITH", "Person") in built.edges


# ------------------------------------------------------------------- torch


def test_hetero_data_round_trip(run, tmp_path):
    torch = pytest.importorskip("torch")
    pytest.importorskip("torch_geometric")
    from graphfaker.sinks.pyg import from_directory, to_hetero_data, write_pyg

    data = to_hetero_data(run.tables, run.truth, seed=1)
    assert data.validate()
    assert data["Account"].y.sum().item() > 0 and data["Account"].train_mask.dtype == torch.bool
    assert data["Account", "TRANSFERS", "Account"].edge_index.shape[1] == run.tables.edges["TRANSFERS"].height
    assert data["Account"].feature_names[-2:] == ["balance", "opened_at"]

    path = write_pyg(run.tables, tmp_path / "bank.pt", run.truth, seed=1)
    loaded = torch.load(path, weights_only=False)
    assert loaded["Account"].x.shape == data["Account"].x.shape
    assert loaded["Account"].node_id[:3] == data["Account"].node_id[:3]

    run.write(tmp_path / "bank")
    again = from_directory(tmp_path / "bank", seed=1)
    assert torch.equal(again["Account"].y, data["Account"].y)
