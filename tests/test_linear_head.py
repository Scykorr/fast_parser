from fast_parser.pipeline.linear_head import LinearHead, fit_softmax


def test_softmax_separates_two_obvious_classes():
    features = [[0.0], [0.05], [0.95], [1.0]]
    labels = [0, 0, 1, 1]
    mean, std, weight, bias = fit_softmax(features, labels, n_classes=2, epochs=60, lr=0.8, l2=0.0)
    head = LinearHead(["левый", "правый"], mean, std, weight, bias)
    predicted = [name for name, _score in head.predict([[0.0], [1.0]])]
    assert predicted == ["левый", "правый"]


def test_shipped_category_head_has_the_twelve_rubrics():
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "src" / "fast_parser" / "pipeline" / "weights" / "category_head.json"
    head = LinearHead.load(path)
    assert len(head.labels) == 12
    assert "Экономика" in head.labels
    assert len(head.weight) == 12
    assert len(head.weight[0]) == len(head.mean)


def test_folds_keep_every_row_once():
    from fast_parser.pipeline.linear_head import stratified_folds

    labels = ["a", "a", "a", "b", "b", "b", "c", "c", "c"]
    folds = stratified_folds(labels, 3, seed=1)
    flat = [index for fold in folds for index in fold]
    assert sorted(flat) == list(range(len(labels)))
