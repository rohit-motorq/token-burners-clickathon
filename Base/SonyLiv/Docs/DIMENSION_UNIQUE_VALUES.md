# Dimension Unique Values — Full Breakdown

Source: `Base/SonyLiv/data/ch-hackathon-raw-data.csv` (905,558 rows, pulled via git-lfs)
Method: full-file scan, exact counts per column.

```python
import csv, collections
cols = ['platform','country','audio_language','subtitle_language','app_version','player_version','event_type','event']
counts = {c: collections.Counter() for c in cols}
with open('ch-hackathon-raw-data.csv', newline='') as f:
    r = csv.reader(f)
    header = next(r)
    idx = {c: header.index(c) for c in cols}
    for row in r:
        for c in cols:
            counts[c][row[idx[c]]] += 1
```

No `os` column in raw data — `platform` covers OS+device combined (e.g. `ANDROID_PHONE`, `SONY_ANDROID_TV`).

---

## platform (10 unique)

| Value | Events |
|---|---|
| ANDROID_PHONE | 629,646 |
| SONY_ANDROID_TV | 79,850 |
| IPHONE | 78,020 |
| JIO_ANDROID_TV | 56,567 |
| Mweb | 16,166 |
| ANDROID_TAB | 13,021 |
| XIAOMI_ANDROID_TV | 10,322 |
| SAMSUNG_HTML_TV | 9,969 |
| FIRE_TV | 7,260 |
| LG_HTML_TV | 4,737 |

## country (1 unique)

| Value | Events |
|---|---|
| india | 905,558 |

100% single-valued in this dataset. Unseen day may differ.

## audio_language (41 unique)

| Value | Events |
|---|---|
| hin | 610,889 |
| eng | 77,360 |
| HIN | 69,033 |
| unk | 51,148 |
| hin-hindi | 23,095 |
| mal | 16,229 |
| tel | 10,394 |
| non | 8,445 |
| tam | 6,968 |
| mar | 6,744 |
| eng-english | 4,900 |
| MAL | 4,284 |
| (empty) | 1,991 |
| kan | 1,963 |
| TEL | 1,823 |
| ENG | 1,522 |
| jap | 1,374 |
| ben | 1,280 |
| oji | 945 |
| TAM | 899 |
| mal-malayalam | 538 |
| hin-Hindi | 507 |
| tel-telugu | 456 |
| jpn | 386 |
| tam-tamil | 372 |
| eng-English | 347 |
| MAR | 313 |
| JPN | 273 |
| OJI | 237 |
| NON | 188 |
| occ | 165 |
| KAN | 127 |
| guj | 120 |
| mar-marathi | 113 |
| ass | 42 |
| UNK | 37 |
| jpn-japanese | 16 |
| -soundhandler | 13 |
| BEN | 10 |
| KOR | 6 |
| kor | 6 |

Messy: casing dupes (`hin`/`HIN`/`hin-Hindi`) and suffix variants (`hin-hindi`) split the same language across multiple keys. Needs lowercase + dedup normalization before use as a filter dimension.

## subtitle_language (11 unique)

| Value | Events |
|---|---|
| UNK | 753,258 |
| UND | 63,768 |
| ENG | 29,042 |
| off | 28,982 |
| OFF | 10,842 |
| unk | 9,902 |
| NON | 5,672 |
| (empty) | 2,006 |
| eng-English | 1,375 |
| AUT | 653 |
| und | 58 |

## app_version (65 unique, top 50 shown)

| Value | Events |
|---|---|
| 6.34.8 | 490,940 |
| 6.34.4 | 89,349 |
| 6.25.1 | 72,693 |
| 3.11.1 | 46,098 |
| 8.9.5 | 39,428 |
| 9.0.1 | 21,200 |
| 6.34.6 | 18,483 |
| 3.8.5 | 16,036 |
| 5.17.39 | 13,774 |
| 9.0.0 | 11,545 |
| 3.9.4 | 10,438 |
| 6.32.4 | 9,856 |
| 6.30.8 | 8,820 |
| 9.38.1 | 7,194 |
| 6.27.1 | 5,214 |
| 6.30.10 | 3,859 |
| 6.23.5 | 3,646 |
| 8.9.4 | 3,456 |
| 6.28.4 | 3,379 |
| 6.36.4 | 2,838 |
| 6.28.14 | 2,536 |
| 6.24.6 | 1,918 |
| 6.19.3 | 1,856 |
| 6.21.1 | 1,752 |
| 6.30.6 | 1,495 |
| 6.28.12 | 1,479 |
| 6.19.4 | 1,459 |
| 6.36.2 | 1,370 |
| 6.28.8 | 1,357 |
| 6.28.10 | 1,173 |
| 6.23.3 | 1,160 |
| 6.15.68 | 1,044 |
| 6.19.1 | 922 |
| 6.28.6 | 903 |
| 6.23.1 | 866 |
| 6.16.14 | 814 |
| 8.9.3 | 639 |
| 5.0.36 | 614 |
| 6.30.4 | 551 |
| 6.17.2 | 547 |
| 8.6.3 | 401 |
| 8.7.3 | 332 |
| 5.0.36.00 | 258 |
| 8.9.0 | 234 |
| 6.22.10 | 170 |
| 8.8.0 | 165 |
| 8.9.1 | 136 |
| 3.8.4 | 130 |
| 6.22.8 | 119 |
| 8.6.10 | 116 |

(15 more app_versions exist below this cutoff with lower counts — full 65-value list available on request.)

## player_version (14 unique)

| Value | Events |
|---|---|
| 1.8.2 | 794,717 |
| 1.1 | 76,486 |
| 3.33.50_ADE | 14,884 |
| 3.29.71_adNE | 8,209 |
| 3.29.71_adE | 5,565 |
| 1.7.0 | 1,918 |
| (empty) | 1,534 |
| 3.33.50_ADNE | 1,152 |
| v-0.0.117.12.05.1_adNE_gaBlocked | 514 |
| v-0.0.117.12.05.1_adNE | 358 |
| 3.32.60_ADE | 130 |
| v-0.0.99-03.06.1-rtv.29_adE | 33 |
| 1.6.0 | 31 |
| v-0.0.99-03.06.1-rtv.29 | 27 |

## event_type (7 unique)

| Value | Events |
|---|---|
| VideoHeartbeat | 843,600 |
| AppBackgrounded | 14,700 |
| AppForegrounded | 14,321 |
| VideoPlay | 10,883 |
| VideoSessionEnd | 10,881 |
| VideoSessionStart | 10,880 |
| VideoError | 293 |

Note: doc `DATA_ANALYSIS-ROHIT.md` §3.1 lists `VideoHeartbeat` as 843,721 — actual full-scan count is 843,600. Difference is negligible (121 rows), doesn't change any conclusions.

## event (47 unique)

| Value | Events |
|---|---|
| network-activity | 177,485 |
| buffer-health | 167,460 |
| video-resize | 141,250 |
| BufferStart | 66,641 |
| BufferEnd | 66,289 |
| video_forward | 49,879 |
| Seek | 32,036 |
| resume | 31,780 |
| network-bandwidth | 30,637 |
| pause | 27,340 |
| upshift | 19,400 |
| AppBackgrounded | 14,700 |
| AppForegrounded | 14,321 |
| dropped-frames | 11,089 |
| Play | 10,883 |
| VideoSessionEnd | 10,881 |
| VideoSessionStart | 10,880 |
| downshift | 7,294 |
| video_rewind | 6,587 |
| AdSkipTrueView | 1,889 |
| network-change | 1,178 |
| download_asset_played | 1,154 |
| next_video_click | 619 |
| go_live_click | 423 |
| download_initiated | 409 |
| speed-change | 399 |
| golive | 396 |
| speed-pause | 380 |
| speed-resume | 380 |
| download_completed | 362 |
| VideoError | 293 |
| audio-language | 180 |
| preroll-disabled | 152 |
| video_quality_change | 144 |
| AdBufferStart | 83 |
| subtitle-language | 83 |
| AdBufferEnd | 62 |
| AdPause | 45 |
| AdResume | 27 |
| preview_watched | 25 |
| download_deleted | 12 |
| download_asset_play_stop | 10 |
| chromecast_clicked | 6 |
| chromecast_started | 6 |
| AdClick | 4 |
| download_resumed | 4 |
| premium_button_click | 1 |

This is fuller than doc §3.2 (only listed top 13 heartbeat sub-events) — includes download, ad, and click-tracking events not previously enumerated.
