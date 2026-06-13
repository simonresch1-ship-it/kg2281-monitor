# KG2281 Restock Monitor

24/7-Cloud-Monitor (GitHub Actions) für die **adidas Deutschland EQT Trainingsjacke KG2281**
(Equipment Green) + die Breuninger-Variante.

- Läuft alle ~5 Minuten als GitHub-Action, unabhängig von jedem lokalen Rechner.
- Überwacht 4 Quellen: Overkill, Asphaltgold, Schrittmacher (Shopify) und Breuninger.
- Pingt per [ntfy.sh](https://ntfy.sh) aufs Handy — **nur** beim Übergang ausverkauft → verfügbar.
- Der Verfügbarkeits-Zustand liegt in `state.json` und wird vom Workflow zurückcommittet,
  damit es zwischen den Läufen kein Spam und keine verpassten Restocks gibt.

Das ntfy-Topic liegt als verschlüsseltes Repo-Secret `NTFY_TOPIC` (nicht im Code).

Manueller Lauf/Test: Tab **Actions → KG2281 Restock Monitor → Run workflow**.
