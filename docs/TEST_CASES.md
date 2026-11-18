# LeadForge: manual test cases

Start the server (CMD):
```cmd
cd /d D:\leadforge && .venv\Scripts\python -m leadforge serve --port 8000
```
Open http://127.0.0.1:8000. Tick each case: ✅ pass / ❌ fail. For a fail, note what you saw.

> 💰 Keep Google costs low: in campaigns use **1 niche, 1–2 cities, "Max businesses per search" = 20**
> (= 1 Places call per search). Check Google Cloud → Billing after testing.

---

## 0. Setup
| # | Steps | Expected |
|---|---|---|
| 0.1 | Open the dashboard | Overview loads, no error |
| 0.2 | Look at the sidebar, "Data sources" | Google Maps · Places API = green dot |
| 0.3 | Settings → API keys | Google Places + Google PageSpeed show a masked key (`AIza••••••…`) |

## 1. First real campaign (Google Places)
| # | Steps | Expected |
|---|---|---|
| 1.1 | New campaign → Services: tick all 4 → Continue | Summary on the right shows 4 services |
| 1.2 | Niches: pick `dentist` → Continue | "Recommended for your services" group appears at the top |
| 1.3 | Locations: country UAE, city Dubai → Continue | Cities are grouped under "United Arab Emirates" |
| 1.4 | Sources: only **Google Maps · Places API** on; Mobile speed test on; Max per search 20 | Serper / Ranking check show "Needs key" and can't be switched on |
| 1.5 | Review | Searches = 1, API calls (max) = 1 |
| 1.6 | Name it `Test Dubai dentists` → Launch | Progress page: Discovering → Auditing websites → PageSpeed → Verifying emails |
| 1.7 | Wait until done | Toast "Campaign finished: X leads, Y hot", ~20 leads found |
| 1.8 | While running, open another page | Top bar shows "Campaign running…"; clicking it returns to progress |

## 2. Leads page
| # | Steps | Expected |
|---|---|---|
| 2.1 | Leads | New Dubai dentists listed, highest score first |
| 2.2 | Change "Best service" → SEO | Score column header = SEO, scores change |
| 2.3 | Tier = Hot / Warm / Cold | Only that tier shown (Hot ≥ 70, Warm 40–69) |
| 2.4 | Search box: type part of a clinic name | Filters as you type |
| 2.5 | Has email switch on | Only leads with email |
| 2.6 | Country / Niche dropdowns | Filters correctly |
| 2.7 | Next / Previous (if > 50 leads) | Pages change, counter "1–50 of N" is right |
| 2.8 | Click a lead row | Drawer opens: contact, pitch, scores with reasons, website audit (speed, mobile, CMS, pixels, chat, booking, socials) |
| 2.9 | Copy pitch | "Pitch copied", paste somewhere to check |
| 2.10 | Change status to Contacted + write a note → Save → reload page (F5) → open again | Status and note are still there |
| 2.11 | Status dropdown directly in the table row | Toast "Marked …" |
| 2.12 | Tick 2–3 rows → "Set status…" = Replied | Toast "3 leads marked Replied" |
| 2.13 | Do not contact on one lead → confirm | Lead becomes Lost; it never appears in future campaigns |

## 3. Export
| # | Steps | Expected |
|---|---|---|
| 3.1 | Export → Excel | .xlsx downloads; tabs Summary / Hot / Warm / All / per service; status + notes columns filled |
| 3.2 | Export → CSV | .csv downloads, opens in Excel |
| 3.3 | Tick 3 rows → Export selected | File has only those 3 leads |
| 3.4 | Filter that matches nothing → Export | Red toast "No leads match these filters" (no broken download) |
| 3.5 | **Google Sheets** (first: enable Drive API + Sheets API, share your sheet with the service-account email as Editor, paste sheet link in Settings → Google Sheet) → Export → Google Sheets | New tab opens your sheet with Hot / Warm / All Leads tabs |

## 4. Custom service
| # | Steps | Expected |
|---|---|---|
| 4.1 | Services → New service: "Social Media Management", signals: No Instagram +30, No Facebook +25, pitch hook with `{niche}` and `{city}` → Create | New card appears, badge "Custom" |
| 4.2 | Edit it, change points | "Best possible score" updates live |
| 4.3 | Run a small campaign with only this service | Leads get a Social Media score with reasons; pitch ends with your hook (e.g. "…for dentists in Dubai…") |
| 4.4 | Switch a built-in service off | It disappears from the campaign wizard |
| 4.5 | Delete the custom service | Removed; built-in services have no Delete button |
| 4.6 | Try to create a service with no signals | Error "Add at least one signal" |

## 5. Niches
| # | Steps | Expected |
|---|---|---|
| 5.1 | Niches → Add niche "yoga studio", link to Social/Chatbot, synonyms "yoga classes" | Appears in list |
| 5.2 | Add a niche that already exists (e.g. dentist) | Error "already exists" |
| 5.3 | Edit → change services / flags | Saved |
| 5.4 | Service filter dropdown | Only niches linked to that service |
| 5.5 | Campaign wizard → Niches → type "pilates studio" → "Add niche" | Created and selected at once |
| 5.6 | Campaign wizard → "Quick add" buttons | Add niche with one click |
| 5.7 | Delete a niche | Gone from list and wizard |

## 6. Locations
| # | Steps | Expected |
|---|---|---|
| 6.1 | Add country: Germany, DE, cities "Berlin, Hamburg, Munich" | Appears; Berlin/Hamburg/Munich have ★ |
| 6.2 | Add country with code "DEU" or empty name | Error message |
| 6.3 | Add cities "Frankfurt, Berlin" | Only Frankfurt added (no duplicate) |
| 6.4 | Remove a city (×) | Removed |
| 6.5 | Switch "Available in campaigns" off | Country no longer in wizard dropdown |
| 6.6 | Wizard → Locations → pick 2 countries → type a new city → Add city | Asks "which country?", then adds it |
| 6.7 | Pick a country but no city | Note "top N cities will be searched"; Review counts N searches |
| 6.8 | Delete Germany | Removed |

## 7. Settings
| # | Steps | Expected |
|---|---|---|
| 7.1 | Workspace name "Aspire Digital" → Save | Sidebar + browser tab show it; export file name starts with it |
| 7.2 | Warm 80, Hot 70 → Save | Error "Warm threshold must be lower than Hot threshold" |
| 7.3 | Hot 60 → Save → Leads | More leads now count as Hot |
| 7.4 | Max Google Places calls = 2 → run a campaign with 3+ searches | Stops after 2 calls (bill protection), log says so |
| 7.5 | Put values back (Hot 70, Warm 40, calls 300) | Saved |

## 8. Campaign history
| # | Steps | Expected |
|---|---|---|
| 8.1 | Campaigns | All runs with found / hot / when |
| 8.2 | Run again on a run | Wizard opens on Review with the same choices, "(again)" in the name |
| 8.3 | Launch it (Only new businesses on) | Log: "Skipped N already-known leads"; ~0 new leads |

## 9. Validation & edge cases
| # | Steps | Expected |
|---|---|---|
| 9.1 | Wizard: Continue with no service / niche / country | Red message on that step, can't continue |
| 9.2 | Pick many niches + countries with 15 top cities so searches > 400 | Review shows "Too big", launch blocked |
| 9.3 | Launch a campaign while one is running (second browser tab) | Error "A campaign is already running" |
| 9.4 | Lead with no website | Shows "No website" badge; high Web Development score |
| 9.5 | Stop server (Ctrl+C) and click around | "Can't reach the LeadForge server" / error toast, no blank page |

## 10. Look & feel
| # | Steps | Expected |
|---|---|---|
| 10.1 | ◐ Theme button | Switches dark / light, remembered after reload |
| 10.2 | Make the browser window narrow (phone width) | ☰ menu appears, no sideways scrolling |
| 10.3 | Press Esc with a drawer / popup open | It closes |

## 11. Command line (optional)
```cmd
.venv\Scripts\python -m leadforge doctor
.venv\Scripts\python -m leadforge run -n dentist -c UAE --city Dubai --limit 20 --service seo,web
.venv\Scripts\python -m leadforge export --min-score 40
```
| # | Expected |
|---|---|
| 11.1 | doctor: Google Places = ready |
| 11.2 | run: table of top leads + "Saved: output\….xlsx" |
| 11.3 | export: Excel from all saved leads with score ≥ 40 |

## 12. Lead quality check (most important)
Open 5 hot leads and check by hand:
- Is the phone / website correct (open it)?
- Are the reasons true (really slow? really no chat / booking? really no Instagram link)?
- Would you send this pitch as is?

Write down any wrong reason. Those tell us which detector to fix.

## 13. After testing
- Google Cloud → Billing: check cost of the test.
