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


# ------------------------------------------------------- other domain packs
#
# The label rules are a convention, not fraud's column names: a truth frame
# keyed by ``<entity>_id`` with one boolean column labels those entities. These
# assert the convention holds for a second pack, because the alternative is
# discovering it does not when someone adds a third.


@pytest.fixture(scope="module")
def coordination_run():
    from graphfaker.domains import coordination

    return coordination.generate(scale=0.0006, tradecraft="medium", seed=3)


@pytest.fixture(scope="module")
def coordination_built(coordination_run):
    return arrays(coordination_run.tables, coordination_run.truth, seed=3)


def test_coordination_node_labels_come_from_is_coordinated(
    coordination_run, coordination_built
):
    account = coordination_built.nodes["Account"]
    assert account.y is not None, "no node labels were written"
    truth = coordination_run.truth["accounts"]
    expected = set(truth.filter(pl.col("is_coordinated"))["account_id"].to_list())
    flagged = {v for v, label in zip(account.ids.tolist(), account.y) if label}
    assert flagged == expected


def test_coordination_decoys_are_marked_not_positive(
    coordination_run, coordination_built
):
    """An organic account is ``decoy=1`` and ``y=0``: legitimate, and labelled."""
    account = coordination_built.nodes["Account"]
    assert account.decoy is not None
    assert int(account.decoy.sum()) > 0
    assert int((account.y & account.decoy).sum()) == 0


def test_coordination_labels_the_event_channels(coordination_built):
    labelled = {
        rel for (_, rel, _), edge in coordination_built.edges.items() if edge.y is not None
    }
    assert {"POSTED", "RESHARED", "REPLIED"} <= labelled
    # FOLLOWS and USES are structure; the truth says nothing about them.
    assert "FOLLOWS" not in labelled


def test_coordination_topics_are_not_mistaken_for_the_labelled_type(
    coordination_built,
):
    """Regression: the ``campaigns`` frame holds real Topic ids next to a
    boolean, so matching on values alone labelled every topic."""
    assert coordination_built.nodes["Topic"].y is None
    assert coordination_built.nodes["Device"].y is None


def test_coordination_community_is_a_latent_factor_not_a_feature(coordination_built):
    account = coordination_built.nodes["Account"]
    assert "community" in account.latent
    assert not any(name.startswith("community") for name in account.feature_names)


def test_identifiers_stay_out_of_edge_attributes(coordination_built):
    """``event_id`` and ``template_id`` are identifiers; a standardised id is a
    meaningless axis. ``topic`` on an interaction edge is a foreign key, and
    one-hot encoding it added 48 columns that would reach thousands at scale."""
    for (_, rel, _), edge in coordination_built.edges.items():
        assert not any(
            name.endswith("_id") or name.startswith("topic=")
            for name in edge.feature_names
        ), (rel, edge.feature_names)


def test_coordination_edge_times_are_present(coordination_built):
    for (_, rel, _), edge in coordination_built.edges.items():
        if rel in {"POSTED", "RESHARED", "REPLIED"}:
            assert edge.edge_time is not None, rel


def test_a_truth_frame_with_two_booleans_is_not_guessed_at():
    """The convention fails loudly rather than picking a column."""
    from graphfaker.sinks.pyg import _single_boolean

    frame = pl.DataFrame({"a_id": ["x"], "one": [True], "two": [False]})
    assert _single_boolean(frame) is None


def test_coordination_hetero_data_round_trip(coordination_run, tmp_path):
    pytest.importorskip("torch_geometric")
    from graphfaker.sinks.pyg import from_directory, write_pyg

    coordination_run.write(tmp_path / "platform")
    written = write_pyg(
        coordination_run.tables, tmp_path / "platform" / "graph.pt", coordination_run.truth
    )
    assert written.exists()
    data = from_directory(tmp_path / "platform")
    assert data["Account"].num_nodes == coordination_run.tables.nodes["Account"].height
    assert int(data["Account"].y.sum()) > 0
    assert hasattr(data["Account"], "train_mask")
