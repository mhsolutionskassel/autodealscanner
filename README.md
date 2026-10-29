# AutoDealScanner – Online-Starter

## 1. Dateien hochladen
Entpacke die ZIP-Datei. Öffne dein Repository `autodealscanner` auf GitHub, wähle **Add file → Upload files**, lade alle Dateien und den Ordner `.github` hoch und klicke **Commit changes**. Die Ordnerstruktur muss erhalten bleiben.

## 2. API-Key sicher speichern
Im Repository **Settings → Secrets and variables → Actions → New repository secret** öffnen. Name: `KLAZ_API_KEY`. Als Wert den aktiven Live-Key von Kleinanzeigen Agent eintragen und **Add secret** anklicken. Den Schlüssel niemals in den Quellcode eintragen oder öffentlich teilen.

## 3. Ersten Suchlauf starten
**Actions → Aktualisiere Autosuche → Run workflow → Run workflow**. Öffne anschließend den Lauf. Grün bedeutet erfolgreich. Der erste Lauf kann zwei zusätzliche API-Aufrufe zur Erkennung von Standort- und Kategorie-ID brauchen, zusätzlich zu drei Suchabfragen.

## 4. Dashboard veröffentlichen
**Settings → Pages → Build and deployment → Deploy from a branch** auswählen. Branch `main`, Ordner `/(root)`, **Save**. GitHub zeigt anschließend die Website-Adresse an.

## Was die Version macht
- Sucht in 100 km um Kassel nach Inseraten bis 7.000 € mit den Suchbegriffen Auto, Gebrauchtwagen und PKW.
- Speichert Treffer und zeigt sie im Browser-Dashboard.
- Versucht, Vergleichspreise aus den gefundenen Inseraten abzuleiten und zieht einen Kosten-/Risikopuffer ab.
- Läuft täglich um 06:17 UTC (07:17 MEZ / 08:17 MESZ) und kann manuell gestartet werden.

## Grenzen
Der API-Anbieter ist ein Drittanbieter. Guthaben, API-Verfügbarkeit und Nutzungsbedingungen richten sich nach seinem aktuellen Angebot. Das Gewinnpotenzial ist nur eine grobe Vorauswahl: Titel-basierte Vergleichsgruppen können Fahrzeuge unterschiedlichen Baujahrs oder Zustands enthalten. Vor einem Kauf sind Inserat, Historie, TÜV, Rost, Motor/Getriebe und echte Vergleichsangebote zu prüfen. Das Dashboard und die Suchergebnisse sind öffentlich, wenn GitHub Pages für dieses Public-Repository aktiviert ist. Der API-Key wird als GitHub Secret gespeichert und nicht veröffentlicht.
