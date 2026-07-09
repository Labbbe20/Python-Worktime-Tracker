# How to use / Bedienung

Private, lokale Arbeitszeiterfassung mit drei Bereichen:

- `main.pyw` / `main.py`: Root-Starter. Standardmäßig startet der Tracker; mit `--app` startet nur die App.
- `tracker/`: Hintergrundprogramm mit Tray-/Menüleisten-Icon, automatischem Arbeitsbeginn beim Start, manuellem Pausen-/Abwesenheitsmenü, Windows-Shutdown-Erkennung und Benachrichtigungen.
- `app/`: lokale pywebview-Desktop-App für Dashboard, Kalender, Einträge, Statistiken, Urlaub/Abwesenheiten, Einstellungen, Backup und Export.
- `common/`: gemeinsame SQLite-Datenbank, Berechnungs-Engine, Standortcheck, HTML-Diagnose-Log und Export.

Es gibt keinen Server, keinen offenen Port, keine Cloud und kein automatisches Backup. Alle Daten liegen lokal in `data/database.db`. In der Windows-Exe liegt der Datenordner neben der Exe.

## Für normale Nutzung unter Windows

Am bequemsten ist die gebündelte Datei:

```text
ArbeitszeitTracker.exe
```

Diese Exe enthält Python und die benötigten Pakete. Auf dem Ziel-PC muss dafür kein `pip install` ausgeführt werden. Beim Start läuft der Tracker im Tray. Die App öffnest du über das Tray-Menü mit `App öffnen`, `Urlaub und Abwesenheiten` oder `Einstellungen`.

Wenn der Code auf GitHub liegt, muss die Exe nicht auf dem Windows-PC gebaut werden:

1. Repository auf GitHub öffnen.
2. `Actions` öffnen.
3. Workflow `Build Windows EXE` auswählen.
4. Falls noch kein Lauf vorhanden ist: `Run workflow` klicken.
5. Den neuesten erfolgreichen Lauf öffnen.
6. Unten bei `Artifacts` die Datei `ArbeitszeitTracker-Windows` herunterladen.
7. ZIP entpacken und `ArbeitszeitTracker.exe` starten.

Das ist der empfohlene Weg für PCs ohne Python-Installation.

Beim ersten Start unter Windows legt der Tracker eine Verknüpfung im persönlichen Autostart-Ordner an:

```text
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
```

Es werden keine Registry-Einträge und keine geplanten Aufgaben verwendet.

## Windows-Exe lokal bauen

Das ist nur für Entwicklung gedacht. Der Build muss auf Windows erfolgen, weil PyInstaller keine Windows-Exe auf macOS erzeugen kann. Auf einem normalen Ziel-PC ist dieser Schritt nicht nötig, wenn du die Exe aus GitHub Actions herunterlädst.

PowerShell im Projektordner:

```powershell
.\build_windows.bat
```

Das Skript installiert die Build-Abhängigkeiten und erzeugt:

```text
dist\ArbeitszeitTracker.exe
```

Nur diese Exe muss danach auf den eigentlichen Arbeits-PC kopiert werden. Der Arbeits-PC braucht dann keine Python-Installation und keine Pakete.

## Entwicklung mit Python

Python 3.11 oder neuer verwenden.

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 main.py
```

Windows:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pythonw main.pyw
```

Nur die App starten:

```bash
python3 main.py --app
```

```powershell
pythonw main.pyw --app
```

Hinweis Windows: pywebview nutzt üblicherweise Microsoft Edge WebView2. Auf aktuellen Firmen-PCs ist die Runtime meistens vorhanden. Falls das App-Fenster nicht startet, muss WebView2 durch die IT bereitgestellt werden. Bei Nutzung der gebauten Exe sind die Python-Pakete enthalten, WebView2 bleibt aber eine Windows-Runtime.

## Wichtige Annahmen

- Wochenarbeitszeit ist frei eingebbar. Ohne individuelle Tageswerte wird sie gleichmäßig auf die ausgewählten Arbeitstage verteilt.
- Eine automatische Pausenzeit kann in Minuten gesetzt werden. Sie ist die Mindestpause pro Tag: ohne manuelle Pause wird sie abgezogen, bei zu kurzer manueller Pause wird bis zur Mindestpause ergänzt, bei längerer manueller Pause zählt die längere Pause.
- Homeoffice/Büro beeinflusst keine Berechnung. Der Standort ist nur Information für Kalender und Statistik.
- Der Standortcheck akzeptiert Servernamen, `host:port` und `http(s)://...`-Ziele.
- Ein Servername ohne Port wird per System-Ping geprüft. `host:port` und `http(s)://...` werden per TCP-Verbindungsversuch geprüft. Wenn der Firmenserver Ping blockiert, verwende einen bekannten offenen Port, z. B. `servername:445`, `servername:443` oder einen von der IT genannten Dienstport.
- Keine Standort-Ziele konfiguriert bedeutet `HOME`. Mindestens ein erreichbares Ziel bedeutet `OFFICE`; kein Ziel erreichbar bedeutet `HOME`. Eine ungültige Ziel-Konfiguration wird als `HOME` behandelt und im Log als Warnung protokolliert.
- Der erste eindeutig erkannte Arbeitsort des Tages (`OFFICE` oder `HOME`) wird für weitere Arbeitssegmente desselben Tages übernommen. Dadurch springt ein Homeoffice-Tag nach späterer VPN-Verbindung nicht plötzlich auf Büro. Gemischte Tage können manuell im Kalender korrigiert werden.
- Feiertage werden über `holidays` anhand des Bundeslands berechnet. Ohne Bundesland gibt es keine automatische Feiertagsmarkierung.
- Sondertage ohne echte Arbeitssegmente sind neutral: Soll gilt als erfüllt, Saldo bleibt 0.
- Halbtägige Sondertage mit echter Arbeit schreiben die halbe Sollzeit gut; die reale Arbeit zählt zusätzlich.
- Urlaub und Krankheit werden für Statistik und Urlaubskonto nur an Tagen mit Sollzeit gezählt.
- Optional kann ein `Startdatum der Zeiterfassung` gesetzt werden. Ohne manuelles Startdatum beginnt die Statistik intern beim ersten echten Eintrag, sonst bei heute.
- Der `Anfangssaldo Gleitzeit in Stunden` wird als Basis zum Saldo ab Startdatum addiert. Positive und negative Kommazahlen mit Komma oder Punkt sind erlaubt, z. B. `50,89`, `-3,75`, `12.5`.
- Offene, noch laufende Segmente werden live im Dashboard angezeigt, aber nicht ins Gleitzeitkonto und nicht in den Monats-Carry-over eingerechnet.
- Beim Herunterfahren versucht Windows direkt Feierabend zu melden. Falls Windows dieses Ereignis nicht zuverlässig liefert, wird beim nächsten Start automatisch mit dem letzten gespeicherten Tracker-Zeitpunkt vom Vortag geschlossen. Nur wenn kein passender Zeitstempel vorhanden ist, erscheint der Nachtragen-Dialog.
- Die Officequote im Dashboard zählt getrackte Büro-/Homeoffice-Tage plus manuelle Nachtragswerte. Gemischte Tage zählen jeweils halb zu Büro und Homeoffice.
- Die gebündelte Windows-Exe und die App verwenden `app/static/icons/app.ico` als Symbol.
- Backups werden ausschließlich manuell ausgelöst und lokal in `data/backups/` abgelegt.

## Bedienung

Tracker-Menü:

- Arbeitsbeginn, wenn kein Segment offen ist
- Pause starten/beenden
- Abwesenheit starten/beenden
- Feierabend
- Urlaub und Abwesenheiten/App öffnen/Einstellungen
- Backup jetzt erstellen
- Diagnose-Log öffnen
- Beenden

Die App speichert Änderungen sofort in SQLite und berechnet `day_summary` und `month_closing` neu.

## Einstellungen

In der App unter `Einstellungen` sind die Optionen in aufklappbare Kacheln gruppiert:

- Wochenarbeitszeit, z. B. `38,7` oder `40`
- automatische Mindest-Pausenzeit in Minuten, z. B. `45`
- Arbeitstage per Montag-bis-Sonntag-Auswahl
- optional individuelle Sollzeiten als JSON, z. B. `{"0":480,"1":480,"2":480,"3":480,"4":360}`
- Bundesland, z. B. `BW`, `BY`, `NRW`
- Urlaubsanspruch und Übertrag
- Startdatum der Zeiterfassung, z. B. `2026-07-01`
- Anfangssaldo Gleitzeit in Stunden, z. B. `12,5`, `50.89` oder `-3,75`
- manuelle Büro-/Homeoffice-Tage für Zeiten vor dem Tracking
- Standort-Ziele, z. B. `intranet.firma.local`, `intranet.firma.local:443` oder `https://intranet.firma.local`
- Timeout in Millisekunden
- Startpuffer je Standort: Büro und Homeoffice können beim automatischen Arbeitsbeginn um eine feste Minutenanzahl vorverlegt werden
- Darkmode

`Zurücksetzen` öffnet einen Dialog mit drei Optionen: nur Einstellungen zurücksetzen, nur Trackingdaten löschen oder alles zurücksetzen. Beim vollständigen Reset wird danach einmalig die Ersteinrichtung mit Startwerten für Urlaub, Gleitzeit und Officequote geöffnet.

### Startdatum und Anfangssaldo

Wenn du mitten im Jahr startest, kannst du in den Einstellungen ein Startdatum setzen. Alle Tage vor diesem Datum werden als `Vor Startdatum` behandelt: keine Sollzeit, keine Istzeit, kein negativer Saldo. Wenn das Feld leer bleibt, verwendet die App automatisch den ersten echten Datensatz als Start.

Der Anfangssaldo wird intern als Minuten gespeichert und mit Decimal-Parsing aus Stunden berechnet. Dadurch werden Eingaben wie `50,89`, `50.89`, `-3,75`, `12,5` und `0` stabil verarbeitet. Angezeigt wird der Stand als Dezimalstunden mit zwei Nachkommastellen, z. B. `+12,50 h`.

### Gleitzeit-Farben

Dashboard und Monatsstatistik markieren den Gleitzeitstand nach der Firmenregel:

- Unter 0 Stunden: rot
- 0 bis einschließlich 45 Stunden: grün
- Über 45 bis einschließlich 50 Stunden: orange
- Über 50 Stunden: rot

Die Logik liegt zentral in `common/balance.py`; die App zeigt dazu Badges mit erklärendem Text an.

## Tests

Automatische Tests:

```bash
python3 -m pytest
```

Getestet werden Datenbank-/Berechnungslogik, Pausenzeit/Arbeitstage, Startdatum/Anfangssaldo, Officequote, Gleitzeit-Farbstatus, Standortcheck, macOS-sichere Notification-Queue, Launcher und HTML-Log-Erzeugung.

## Manuelle Testanleitung

### macOS Entwicklung

1. `python3 main.py` starten.
2. Prüfen, dass ein Menüleisten-Icon erscheint.
3. Nach dem Start prüfen, dass ein Arbeitsbeginn-Segment in der App sichtbar ist.
4. `Pause starten` klicken und prüfen, dass der Tracker nicht mit `NSWindow should only be instantiated on the main thread` abstürzt.
5. `Pause beenden` klicken, prüfen, dass automatisch ein neues Arbeitssegment startet.
6. `Abwesenheit starten/beenden` analog testen.
7. `Feierabend` klicken und prüfen, dass das offene Segment geschlossen wird.
8. `python3 main.py --app` starten und Dashboard, Kalender, Einträge, Statistiken, Urlaub und Abwesenheiten sowie Einstellungen öffnen.
9. In den Einstellungen die Kacheln `Arbeitsmodell`, `Startwerte`, `Standort & Puffer`, `Backup & Export` und `Zurücksetzen` öffnen und schließen.
10. `Pausenzeit`, `Arbeitstage`, `Startdatum der Zeiterfassung`, `Anfangssaldo Gleitzeit in Stunden`, manuelle Büro-/Homeoffice-Tage und Startpuffer je Standort testen.
11. Dashboard und Statistiken prüfen: Gleitzeitstand soll als Badge grün/orange/rot erscheinen, Dashboard-Kacheln sollen Details aufklappen.
12. Dashboard `Officequote` prüfen: Büroanteil soll mit Details zu Büro, Homeoffice, getrackten und manuellen Tagen aufklappen.
13. Im Kalender prüfen, dass links die Kalenderwochen stehen; dann einen Tag öffnen, Segmentzeiten ändern, Standort korrigieren und Notiz speichern.
14. In `Einträge` eine Zeile über `Bearbeiten` öffnen, Segment ändern/löschen und prüfen, dass die Liste aktualisiert wird.
15. Backup über App oder Tray auslösen und Datei in `data/backups/` prüfen.
16. Diagnose-Log öffnen und Suche/Sortierung im Browser testen.
17. In `Urlaub und Abwesenheiten` Urlaub, Gleitzeit, Krankheit, Dienstreise oder Feiertags-Ausnahme mit Notiz eintragen und prüfen, dass darunter Zeitraum, Notiz, angerechnete Arbeitstage und Entfernen-Aktion erscheinen.
18. Export in CSV, Excel und PDF ausführen.
19. Aus dem Tracker-Menü mehrmals `App öffnen`, `Urlaub und Abwesenheiten` und `Einstellungen` wählen. Es darf immer nur ein App-Fenster laufen.

### Windows Produktivtest

1. `ArbeitszeitTracker.exe` starten.
2. Prüfen, dass die Autostart-Verknüpfung im persönlichen Startup-Ordner liegt.
3. Windows neu starten. Nach Login muss automatisch Arbeitsbeginn erfasst werden.
4. Bildschirm mit `Win+L` sperren und wieder entsperren. Es darf kein neues Segment, keine Pause und keine Abwesenheit entstehen.
5. Pause/Abwesenheit ausschließlich über das Tray-Menü starten/beenden.
6. Windows herunterfahren oder neu starten. Beim nächsten Start prüfen, dass Feierabend automatisch direkt oder per letztem Tracker-Zeitstempel nachgetragen wurde.
7. Crash-Recovery testen: Tracker während offenem Segment hart beenden, Datum ggf. auf Folgetag testen, Tracker starten und Dialog zum Nachtragen prüfen.
8. App aus dem Tray öffnen und prüfen, dass in der Taskleiste das ArbeitszeitTracker-Symbol statt des Python-Symbols erscheint.
9. Standortcheck mit einem nur im Firmennetz oder per VPN erreichbaren Server testen.
10. App parallel zum Tracker geöffnet lassen und Änderungen in beiden Programmen prüfen.

## Daten und Dateien

- Datenbank: `data/database.db`
- Backups: `data/backups/database_backup_YYYYMMDD_HHMMSS.db`
- Diagnose-Logs: `data/logs/log_YYYY-MM.html`
- Exporte: `data/exports/`

## Windows-only Module

- `tracker/autostart_windows.py`: Verknüpfung im persönlichen Autostart-Ordner.
- `tracker/shutdown_windows.py`: reagiert auf `WM_QUERYENDSESSION`/`WM_ENDSESSION`.
- `tracker/main.py`: schreibt regelmäßig einen lokalen Tracker-Zeitstempel als Fallback, falls Windows kein Shutdown-Ereignis liefert.

Bildschirmsperre wird nicht registriert und beeinflusst Tracking nicht.
