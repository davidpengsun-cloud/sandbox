# Tagger evaluation

Mock plumbing check; not model quality evidence

Set: tagger-v1; SHA256: `f7782694d10c5baa70e8b26bdec1e5e6b594896fe08e04d782b5538a26cc9359`; repeats: 1.

Counts are unique (passage, label) pairs per repeat; duplicates within a passage count once.

| Arm | Family | Expected | Found | Wrong | Missed |
|---|---|---:|---:|---:|---:|
| RuleTagger | candidates | 5 | 2 | 2 | 3 |
| RuleTagger | mentions | 8 | 8 | 0 | 0 |
| RuleTagger | surface_mentions | 8 | 8 | 0 | 0 |
| low | candidates | 5 | 2 | 2 | 3 |
| low | mentions | 8 | 8 | 0 | 0 |
| low | surface_mentions | 8 | 8 | 0 | 0 |
| medium | candidates | 5 | 2 | 2 | 3 |
| medium | mentions | 8 | 8 | 0 | 0 |
| medium | surface_mentions | 8 | 8 | 0 | 0 |
| high | candidates | 5 | 2 | 2 | 3 |
| high | mentions | 8 | 8 | 0 | 0 |
| high | surface_mentions | 8 | 8 | 0 | 0 |

Targets: owner agreement of 2026-11-02, recorded in result.json

RuleTagger: FAIL (candidate_wrong_rate)
low: FAIL (candidate_recall, candidate_wrong_rate)
medium: FAIL (candidate_recall, candidate_wrong_rate)
high: FAIL (candidate_recall, candidate_wrong_rate)
