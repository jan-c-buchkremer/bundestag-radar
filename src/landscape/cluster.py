"""UMAP layout, HDBSCAN clusters and c-TF-IDF cluster labels."""

from __future__ import annotations

import statistics
from dataclasses import dataclass

import numpy as np

# German function words and parliamentary boilerplate. max_df catches "und", "die", "wir"; these are the
# mid-frequency words ("wollen", "doch", "dieses", "prozent") that would otherwise top every cluster label.
STOPWORDS = """
aber alle allem allen aller alles als also anderen anderer anderes auch bereits beim bevor bin bis bisher bitte
brauchen bzw dabei dafür dagegen daher damit danach dann daran darauf daraus darin darüber darum das dass daß
davon dazu dein dem den denen denn dennoch der deren des deshalb dessen deswegen dich die dies diese diesem diesen
dieser dieses doch dort durch eben eher eigentlich ein eine einem einen einer eines einfach einmal etwa etwas euch
euro ganz gar geht gehen gerade gern gestern gibt gilt gut gute guten haben hast hat hatte hatten hätte hätten heute
hier hin hinter ich ihm ihn ihnen ihr ihre ihrem ihren ihrer ihres immer indem ins ist jede jedem jeden jeder jedes
jetzt kann kaum kein keine keinem keinen keiner können könnte könnten lassen lässt letzten liegt machen macht mal man
mehr mein meine mich mir mit muss müssen musste nach nämlich natürlich neben nein neue neuen nicht nichts noch nun
nur oder ohne prozent recht richtig sagen sagt schon sehr sei seien sein seine seinem seinen seiner seit selbst
sich sicher sie sind sogar solche sollen sollte sollten sondern sonst soweit sowie später statt tag tagen tage
tatsächlich tun über überhaupt und uns unser unsere unserem unseren unserer unseres viel viele vielen vielleicht
vom von vor wann war waren was weg weil weiter weitere weiteren welche welchem welchen welcher welches wenig weniger
wenn wer werden wieder will wir wird wirklich wissen wollen wollte worden wurde wurden würde würden zum zur zwar
zwei zwischen jahr jahre jahren milliarden millionen
geehrte geehrter geehrten liebe lieber erste ersten zweite zweiten dritte
präsident präsidentin kollege kollegin kolleginnen kollegen damen herren abgeordnete abgeordneten abgeordneter
fraktion bundesregierung koalition antrag gesetz gesetzentwurf gesetzes ausschuss minister ministerin
deutschland land bürgerinnen bürger menschen zwischenfrage frage antwort union afd grünen linke
habe gegen genau endlich politik bundestag
""".split()  # noqa: SIM905


@dataclass
class Clustering:
    xy: np.ndarray  # (n, 2) map coordinates
    labels: np.ndarray  # (n,) cluster id per speech, -1 = noise
    terms: dict[int, list[str]]  # cluster id -> top terms


def cluster(vectors: np.ndarray, texts: list[str], min_cluster_size: int = 8, seed: int = 42) -> Clustering:
    import umap  # slow imports (numba, sklearn); keep `landscape weeks` fast
    from sklearn.cluster import HDBSCAN

    if len(vectors) < 2 * min_cluster_size:  # ceremonial single sittings: no clusters, plain SVD layout
        xy = np.linalg.svd(vectors - vectors.mean(axis=0), full_matrices=False)[0][:, :2]
        return Clustering(xy=xy, labels=np.full(len(vectors), -1), terms={})
    low = umap.UMAP(n_components=5, n_neighbors=15, metric="cosine", random_state=seed).fit_transform(vectors)
    xy = umap.UMAP(n_components=2, n_neighbors=15, metric="cosine", random_state=seed).fit_transform(vectors)
    # "leaf": budget weeks otherwise collapse into one cluster of 470 speeches; identical on normal weeks
    labels = HDBSCAN(min_cluster_size=min_cluster_size, cluster_selection_method="leaf").fit_predict(low)
    return Clustering(xy=np.asarray(xy), labels=labels, terms=cluster_terms(texts, labels))


def neighbours(vectors: np.ndarray, k: int = 5) -> list[list[int]]:
    """Indices of the k most similar speeches for each speech (cosine on unit vectors)."""
    sims = vectors @ vectors.T
    np.fill_diagonal(sims, -1)
    k = min(k, len(vectors) - 1)
    return np.argsort(-sims, axis=1)[:, :k].tolist() if k > 0 else [[] for _ in vectors]


def cluster_terms(texts: list[str], labels: np.ndarray, top: int = 4) -> dict[int, list[str]]:
    """c-TF-IDF (BERTopic style): term frequency per cluster × log(1 + avg cluster size / global term count)."""
    from sklearn.feature_extraction.text import CountVectorizer

    vec = CountVectorizer(token_pattern=r"(?u)\b[^\W\d_]{3,}\b", stop_words=STOPWORDS, min_df=2, max_df=0.5)
    counts = vec.fit_transform(texts)
    vocab = vec.get_feature_names_out()
    ids = np.unique(labels[labels >= 0]).tolist()
    if not ids or not len(vocab):
        return {}
    per_cluster = np.vstack([np.asarray(counts[labels == c].sum(axis=0)).ravel() for c in ids])
    tf = per_cluster / np.maximum(per_cluster.sum(axis=1, keepdims=True), 1)
    idf = np.log1p(per_cluster.sum() / len(ids) / np.maximum(np.asarray(counts.sum(axis=0)).ravel(), 1))
    scores = tf * idf
    return {c: [str(vocab[i]) for i in np.argsort(-scores[k])[:top]] for k, c in enumerate(ids)}


def majority(values: list[str], labels: np.ndarray) -> dict[int, str]:
    """Most frequent value per cluster (used for the agenda-item label)."""
    return {
        c: statistics.mode(v for v, lab in zip(values, labels, strict=True) if lab == c)
        for c in np.unique(labels[labels >= 0]).tolist()
    }
