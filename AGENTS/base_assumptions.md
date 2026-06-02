# base_assumptions.md

## Wytyczne bazowe dla agenta kodującego: autonomiczny moduł analizy działek i serwer MCP dla Claude Code

**Wersja dokumentu:** 1.0  
**Data odniesienia:** 2026-06-02  
**Domyślna jurysdykcja:** Polska  
**Domyślny język domeny:** polski  
**Docelowy klient agenta:** Claude Code przez MCP  
**Priorytet produktu:** szybkość działania, autonomiczność, kompletność due diligence, audytowalność źródeł, jawny poziom pewności.

Dokument jest specyfikacją bazową dla agenta kodującego. Należy traktować go jako kontrakt produktowo-architektoniczny: opisuje zakres funkcjonalny, pytania architekta analizującego działkę, oczekiwane odpowiedzi systemu, architekturę modułów, serwer MCP, model danych, źródła, wydajność, bezpieczeństwo, testy i kryteria akceptacji.

---

## 1. Cel systemu

System ma automatycznie analizować działkę budowlaną albo teren inwestycji składający się z wielu działek. Wynik ma odpowiadać na pytania architekta i inwestora: czy działka nadaje się pod planowaną inwestycję, co można na niej zaprojektować, jakie są ograniczenia, gdzie może powstać zabudowa, jakie ryzyka należy zweryfikować, jakie dokumenty trzeba pozyskać i jakie decyzje administracyjne mogą być potrzebne.

System nie zastępuje decyzji administracyjnej, opinii prawnej, projektu budowlanego, opracowania geotechnicznego, mapy do celów projektowych ani pracy uprawnionego projektanta. Każdy wniosek musi wskazywać źródło i poziom pewności. Gdy dane są niepełne, system ma powiedzieć „nie wiadomo” albo „wymaga potwierdzenia”, zamiast generować pozorną pewność.

Najważniejsza ścieżka użytkownika:

1. Użytkownik podaje identyfikator działki, adres, punkt na mapie, plik GML/SHP/GeoJSON/DXF albo PDF dokumentu planistycznego.
2. System identyfikuje działkę, pobiera geometrię i kontekst administracyjny.
3. System pobiera warstwy planistyczne, środowiskowe, hydrologiczne, geologiczne, topograficzne, drogowe i infrastrukturalne.
4. System uruchamia silnik reguł, analizy przestrzenne i scoring ryzyk.
5. System generuje buildable envelope, warianty chłonności, listę czerwonych flag i checklistę dalszych działań.
6. System zwraca raport tekstowy, warstwy GIS/CAD oraz zasoby MCP do dalszej pracy w Claude Code.

---

## 2. Założenia nadrzędne

### 2.1. Założenia produktowe

- System działa jako biblioteka domenowa, API HTTP i serwer MCP.
- MCP jest interfejsem pierwszej klasy, nie dodatkiem.
- Analizy muszą być powtarzalne: identyczny input, identyczne snapshoty danych i identyczny ruleset dają identyczny wynik.
- Każdy wynik ma zawierać evidence pack: źródło, datę pobrania, warstwę, identyfikator obiektu, wersję reguły, metodę analizy i poziom ufności.
- System ma rozróżniać: fakt źródłowy, interpretację, wniosek, rekomendację i hipotezę.
- System obsługuje tryby: `quick_screening`, `full_due_diligence`, `design_feasibility`, `portfolio_batch`, `monitoring_changes`.
- Reguły prawne, techniczno-budowlane, planistyczne i branżowe muszą być wersjonowane poza kodem aplikacji, np. w `rulesets/*.yaml`.
- System jest „Poland-first”, ale ma architekturę pluginową: `country`, `jurisdiction`, `municipality_id`, `ruleset_version`, `source_profile`.
- Brak danych w usłudze publicznej nie oznacza braku ograniczenia. Raport musi rozróżniać `not_detected`, `confirmed_absent`, `unknown`, `source_unavailable`, `manual_review_required`.
- System musi działać autonomicznie: sam dobiera źródła, pobiera dane, robi fallback, ocenia braki i generuje dalsze zadania.
- System musi działać szybko: wolne integracje nie mogą blokować całej analizy; wymagane są cache, indeksy przestrzenne, snapshoty i degradacja wyników.

### 2.2. Założenia domenowe

- Podstawową jednostką analizy jest działka ewidencyjna albo zbiór działek tworzących teren inwestycji.
- Granica ewidencyjna nie jest tożsama z granicą ustaloną prawnie w terenie. System musi komunikować ograniczenia dokładności danych.
- „Działka budowlana” jest pojęciem wielowymiarowym: przeznaczenie planistyczne, możliwość uzyskania WZ, dostęp do drogi publicznej, możliwość technicznego uzbrojenia, geometria, ograniczenia środowiskowe, warunki gruntowo-wodne i prawo do dysponowania nieruchomością.
- Dane planistyczne są zmienne w czasie. System musi obsługiwać MPZP, plan ogólny gminy, WZ/ULICP, projekty planów, ZPI, obszary uzupełnienia zabudowy, lokalne standardy urbanistyczne i Rejestr Urbanistyczny.
- Dane publiczne bywają poglądowe, niepełne lub opóźnione. Wnioski muszą mieć poziom pewności i listę danych do potwierdzenia.

---

## 3. Definicje operacyjne

- **Działka:** działka ewidencyjna z EGiB lub jej geometria dostarczona przez użytkownika.
- **Teren inwestycji:** jedna albo wiele działek analizowanych łącznie.
- **Buildable envelope:** obszar potencjalnie możliwej lokalizacji zabudowy po uwzględnieniu ograniczeń planistycznych, odległościowych, środowiskowych, technicznych i geometrii działki.
- **No-build zone:** obszar wyłączony albo potencjalnie wyłączony z zabudowy.
- **Evidence pack:** zestaw dowodów źródłowych dla wyniku.
- **Confidence:** poziom zaufania do wyniku, liczony z jakości źródła, aktualności, kompletności, rozbieżności i rodzaju analizy.
- **Ruleset:** wersjonowany zbiór reguł obliczeniowych i interpretacyjnych.
- **Connector:** moduł pobierający dane ze źródła zewnętrznego lub pliku użytkownika.
- **Analysis run:** pojedyncze, wersjonowane uruchomienie analizy.
- **Red flag:** ryzyko, które może zablokować albo znacząco opóźnić inwestycję.
- **Soft risk:** ryzyko kosztowe, proceduralne lub projektowe, które nie musi blokować inwestycji, ale wymaga decyzji.
- **Hard blocker:** ograniczenie potencjalnie uniemożliwiające realizację zakładanego programu.

---

## 4. Tryby pracy

### 4.1. `quick_screening`

Cel: szybka ocena, czy działka rokuje.

Minimalny wynik:

- identyfikacja działki;
- powierzchnia, obwód, kształt, front, szerokość;
- sprawdzenie pokrycia MPZP/POG/WZ, jeśli dostępne;
- przecięcia z najważniejszymi warstwami ryzyk: powódź, ochrona przyrody, zabytki, osuwiska, drogi, kolej, cieki, las, sieci;
- wstępny buildable envelope;
- ranking czerwonych flag;
- decyzja: `OK`, `OK_WITH_RISKS`, `NEEDS_MANUAL_REVIEW`, `LIKELY_BLOCKED`.

### 4.2. `full_due_diligence`

Cel: analiza przed zakupem albo decyzją inwestycyjną.

Wynik rozszerzony:

- wszystkie elementy `quick_screening`;
- interpretacja dokumentów planistycznych;
- parametry urbanistyczne i architektoniczne;
- analiza infrastruktury i dojazdu;
- analiza warunków terenowych i środowiskowych;
- analiza proceduralna;
- szacunkowa chłonność;
- lista dokumentów do pozyskania;
- lista pytań do urzędu, gestorów, geodety, geotechnika, prawnika i architekta prowadzącego;
- raport oraz warstwy GIS/CAD.

### 4.3. `design_feasibility`

Cel: wsparcie architekta przy koncepcji.

Wynik:

- buildable envelope;
- warianty lokalizacji budynku;
- warianty programu;
- analiza orientacji, słońca, cienia, dojazdu, parkingów, retencji i zieleni;
- eksport DXF/GeoPackage/IFC massing opcjonalnie.

### 4.4. `portfolio_batch`

Cel: porównanie wielu działek.

Wynik:

- analiza wsadowa;
- ranking działek;
- scoring inwestycyjny;
- mapa portfela;
- tabela czerwonych flag;
- identyfikacja działek wymagających ręcznego przeglądu;
- eksport CSV/XLSX/GeoPackage/JSON.

### 4.5. `monitoring_changes`

Cel: śledzenie zmian danych i ryzyk.

Wynik:

- alerty o zmianach MPZP/POG/projektów planów;
- alerty o zmianach warstw środowiskowych i hydrologicznych;
- porównanie wyników analizy między datami;
- archiwum snapshotów.

---

## 5. Hierarchia wiarygodności źródeł

System musi stosować jawny ranking źródeł:

1. Akty prawa miejscowego, decyzje administracyjne i dokumenty urzędowe właściwego organu.
2. Dane przestrzenne APP/GML publikowane przez gminę albo właściwy rejestr.
3. Oficjalne rejestry i usługi państwowe: GUGiK, GDOŚ, Wody Polskie/ISOK, PIG-PIB, NID, GUS, PRG, TERYT, GESUT/KIUT.
4. Lokalne BIP, lokalne SIP/geoportale i usługi powiatowe/gminne.
5. Dane gestorów sieci i zarządców dróg.
6. Dokumenty użytkownika: wypisy, wyrysy, decyzje, mapy, skany, DWG/DXF, PDF.
7. Dane pomocnicze: OSM, zdjęcia, dane komercyjne, ogłoszenia, modele ML.

Każdy rekord źródłowy musi mieć co najmniej:

```yaml
source_id: string
source_type: official_register | local_sip | user_document | commercial | auxiliary
publisher: string
url_or_origin: string
retrieved_at: datetime
valid_from: date | null
valid_to: date | null
license: string | unknown
legal_status: binding | informative | auxiliary | unknown
geometry_precision: survey | cadastral | topographic | raster_derived | approximate | unknown
freshness: current | stale | archived | unknown
confidence: 0.0-1.0
notes: string
```

---

## 6. Źródła startowe do implementacji connectorów

Poniżej lista źródeł, które agent kodujący powinien potraktować jako pierwsze cele integracyjne. Przed implementacją należy sprawdzić aktualny regulamin, limity i dokumentację techniczną.

### 6.1. Geodezja, granice, topografia, rastry

- Geoportal / EGiB / ULDK: https://www.geoportal.gov.pl/pl/dane/ewidencja-gruntow-i-budynkow-egib/
- Geoportal / WMS i WMTS: https://www.geoportal.gov.pl/pl/usluga/uslugi-przegladania-wms-i-wmts/
- Geoportal / WFS: https://www.geoportal.gov.pl/pl/usluga/uslugi-pobierania-wfs/
- Geoportal / WCS: https://www.geoportal.gov.pl/pl/usluga/uslugi-pobierania-wcs/
- Geoportal / NMT: https://www.geoportal.gov.pl/pl/dane/numeryczny-model-terenu-nmt/
- Geoportal / NMPT: https://www.geoportal.gov.pl/pl/dane/numeryczny-model-pokrycia-terenu-nmpt/
- Geoportal / BDOT10k: https://www.geoportal.gov.pl/pl/dane/baza-danych-obiektow-topograficznych-bdot10k/
- Geoportal / GESUT/KIUT: https://www.geoportal.gov.pl/pl/dane/uzbrojenie-terenu-gesut/

### 6.2. Planowanie przestrzenne

- Przeglądarka danych planistycznych / APP: https://www.gov.pl/web/gov/sprawdz-poprawnosc-danych-przestrzennych-oraz-metadanych
- Zagospodarowanie przestrzenne / POG: https://www.gov.pl/web/zagospodarowanieprzestrzenne/szybki-start--pog
- Obowiązujące regulacje APP: https://www.gov.pl/web/zagospodarowanieprzestrzenne/standaryzacja--obowiazujace-regulacje2
- Rejestr Urbanistyczny: https://www.gov.pl/web/cyfryzacja/rejestr-urbanistyczny---ru
- Informacja dla JST o RU: https://www.gov.pl/web/zagospodarowanieprzestrzenne/informacja-dla-jst-w-sprawie-rejestru-urbanistycznego

### 6.3. Wody, środowisko, geologia, zabytki

- Hydroportal / ISOK: https://wody.isok.gov.pl/imap_kzgw/
- Usługi INSPIRE Wód Polskich: https://wody.isok.gov.pl/geonetwork/srv/search
- Geoserwis GDOŚ: https://geoserwis.gdos.gov.pl/mapy/
- CBDG / PIG-PIB usługi GIS: https://baza.pgi.gov.pl/geoportal/uslugi/gis
- SOPO: https://www.pgi.gov.pl/osuwiska/123/aplikacja.html
- Rejestr Obszarów Górniczych / MIDAS: https://www.pgi.gov.pl/dane-geologiczne/825-sluzba-geologiczna/baza-rog/8167-rejestr-obszarow-gorniczych-na-psg.html
- Portal mapowy NID: https://mapy.zabytek.gov.pl/

### 6.4. MCP i Claude Code

- MCP introduction: https://modelcontextprotocol.io/docs/getting-started/intro
- MCP tools specification: https://modelcontextprotocol.io/specification/2025-06-18/server/tools
- MCP Python SDK: https://github.com/modelcontextprotocol/python-sdk
- Claude MCP connector docs: https://platform.claude.com/docs/en/agents-and-tools/mcp-connector
- Claude Code remote MCP: https://claude.com/blog/claude-code-remote-mcp
- Anthropic engineering note on many MCP tools and token efficiency: https://www.anthropic.com/engineering/code-execution-with-mcp

---
## 7. Pytania architekta i odpowiedzi systemu

### 7.1. Identyfikacja działki i kontekst administracyjny

**Pytania architekta:**

- Jaka jest dokładna działka i czy identyfikator jest jednoznaczny?
- W jakiej gminie, obrębie, powiecie i województwie leży działka?
- Czy działka istnieje w aktualnych danych?
- Czy działka składa się z wielu części?
- Czy teren inwestycji obejmuje jedną działkę czy kilka działek?
- Czy działka przecina granicę obrębu, gminy, planu albo strefy?
- Czy numer działki mógł zmienić się przez podział, scalenie lub modernizację EGiB?

**Wymagane odpowiedzi/funkcje systemu:**

- resolve parcel by full ID, number, address, point and uploaded geometry.
- normalize identifiers, TERYT and cadastral context.
- validate topology, CRS and geometry quality.
- detect multipart, enclaves, gaps and inconsistent areas.
- create immutable analysis area snapshot.
- flag ambiguity and request clarification only when autonomous resolution fails.

### 7.2. Własność i prawa do terenu

**Pytania architekta:**

- Kto jest właścicielem albo użytkownikiem wieczystym?
- Czy istnieją służebności przechodu, przejazdu lub przesyłu?
- Czy inwestor ma prawo do dysponowania nieruchomością na cele budowlane?
- Czy są ograniczenia pierwokupu, dzierżawy, roszczenia, hipoteki albo spory?
- Czy publiczne dane GIS wystarczają do oceny stanu prawnego?

**Wymagane odpowiedzi/funkcje systemu:**

- generate legal due diligence checklist.
- parse user-supplied KW, wypis, wyrys and decisions when provided.
- flag missing ownership data as manual/legal review.
- detect possible preemption contexts from land use, forest, agriculture, revitalization and public-purpose layers.
- generate questions for lawyer, notary and seller.

### 7.3. Przeznaczenie planistyczne

**Pytania architekta:**

- Czy działka jest objęta MPZP?
- Czy działka jest objęta planem ogólnym gminy?
- Czy istnieje decyzja WZ lub ULICP?
- Czy plan jest obowiązujący, projektowany, zmieniany czy uchylony?
- Czy działka leży w kilku jednostkach planistycznych?
- Jakie jest przeznaczenie podstawowe, dopuszczalne, uzupełniające i zakazane?
- Czy można zrealizować zakładany typ inwestycji?

**Wymagane odpowiedzi/funkcje systemu:**

- detect MPZP/POG/WZ coverage.
- fetch APP/GML and local BIP documents.
- intersect parcel with planning zones.
- parse plan text and drawings with evidence/confidence.
- build allowed/conditional/forbidden use matrix.
- track planning validity, procedure stage and conflicts.
- support Rejestr Urbanistyczny and reform transition logic.

### 7.4. Parametry urbanistyczne

**Pytania architekta:**

- Jaka jest maksymalna powierzchnia zabudowy?
- Jaka jest minimalna powierzchnia biologicznie czynna?
- Jaka jest maksymalna i minimalna intensywność zabudowy?
- Jaka jest maksymalna wysokość i liczba kondygnacji?
- Jaki dach, kąt, kalenica, kolorystyka i materiały są wymagane?
- Jakie są wskaźniki parkingowe?
- Czy działka spełnia minimalne parametry powierzchni i frontu?

**Wymagane odpowiedzi/funkcje systemu:**

- extract indicators from planning documents.
- normalize local definitions of terms.
- compute maximum building footprint, GFA and PUM/PUU ranges.
- compute biologically active area and parking demand.
- detect material, roof, height and façade constraints.
- generate sensitivity analysis for ambiguous indicators.

### 7.5. Linie zabudowy i buildable envelope

**Pytania architekta:**

- Gdzie są nieprzekraczalne i obowiązujące linie zabudowy?
- Czy linie są cyfrowe, odczytane z PDF czy tylko przybliżone?
- Jaka część działki pozostaje po odsunięciach i strefach wyłączonych?
- Czy można sytuować budynek w granicy lub 1,5 m od granicy?
- Czy ściany z oknami i bez okien spełnią odległości?
- Czy po wszystkich ograniczeniach pozostaje realny obszar pod budynek?

**Wymagane odpowiedzi/funkcje systemu:**

- import/vectorize building lines.
- compute setbacks and no-build zones.
- combine statutory, planning and technical buffers.
- calculate buildable polygon and largest inscribed rectangles.
- generate wall/window scenarios.
- rank buildable envelope confidence by source type.

### 7.6. Geometria i przydatność kształtu

**Pytania architekta:**

- Jaka jest powierzchnia, obwód i zwartość działki?
- Jaki jest front, minimalna szerokość i profil szerokości?
- Czy działka jest narożna, wąska, długa, trójkątna, klinowa albo nieregularna?
- Czy granice i załamania komplikują projekt?
- Czy są fragmenty nieużyteczne?

**Wymagane odpowiedzi/funkcje systemu:**

- compute area, perimeter, compactness, convexity and irregularity.
- detect frontage, corner status and main axis.
- calculate width profile and narrowest passage.
- classify boundary edges by neighbor type.
- estimate usable area before and after constraints.

### 7.7. Dostęp do drogi i obsługa komunikacyjna

**Pytania architekta:**

- Czy działka ma dostęp do drogi publicznej?
- Czy dostęp jest bezpośredni czy przez drogę wewnętrzną/służebność?
- Czy zjazd jest możliwy i czy istnieje?
- Jaka jest klasa drogi i kto jest zarządcą?
- Czy droga wymaga poszerzenia albo działka jest w rezerwie drogowej?
- Czy dojazd ppoż., śmieciarka, dostawy i budowa są realne?

**Wymagane odpowiedzi/funkcje systemu:**

- detect public road adjacency and frontage length.
- trace internal-road/servitude access chain.
- detect existing driveway from maps/orthophoto/user files.
- estimate driveway feasibility and road widening risks.
- precheck fire access and turning radii.
- generate questions to road authority.

### 7.8. Uzbrojenie i media

**Pytania architekta:**

- Czy jest prąd, woda, kanalizacja sanitarna, deszczowa, gaz, ciepło, telekomunikacja?
- Czy sieci przebiegają przez działkę?
- Czy istnieją strefy techniczne i kolizje z zabudową?
- Czy przyłączenie jest realne i na jaką odległość?
- Czy potrzebna będzie studnia, szambo, oczyszczalnia, retencja albo alternatywne źródło energii?

**Wymagane odpowiedzi/funkcje systemu:**

- fetch GESUT/KIUT and local utility layers.
- detect networks crossing parcel and buildable envelope.
- compute nearest utility distances.
- apply configurable buffer rules by network type.
- score connection risk and capacity unknowns.
- generate gestor questionnaires and collision map.

### 7.9. Topografia i odwodnienie

**Pytania architekta:**

- Czy działka jest płaska?
- Jaki jest spadek i ekspozycja terenu?
- Czy są lokalne zagłębienia i ryzyko podtopień?
- Czy potrzebne będą mury oporowe lub znaczna niwelacja?
- Czy garaż podziemny lub podpiwniczenie są racjonalne?
- Jak powiązać poziom budynku z drogą?

**Wymagane odpowiedzi/funkcje systemu:**

- fetch DEM/NMT/NMPT/LiDAR.
- compute slope, aspect, contours and elevation profiles.
- detect depressions and flow direction.
- estimate earthworks and retaining-wall risk.
- precheck basement/underground parking feasibility.
- generate terrain diagnostics maps.

### 7.10. Wody, powódź i retencja

**Pytania architekta:**

- Czy działka leży na obszarze szczególnego zagrożenia powodzią?
- Jaki scenariusz powodziowy dotyczy działki?
- Czy są cieki, rowy, wały, jeziora, strefy ujęć wody?
- Czy wymagane będzie pozwolenie lub zgłoszenie wodnoprawne?
- Czy wody opadowe można zagospodarować na działce?
- Czy wymagana jest retencja lub ograniczenie odpływu?

**Wymagane odpowiedzi/funkcje systemu:**

- intersect with flood hazard/risk layers.
- summarize watercourse distances and drainage constraints.
- precheck water-law procedures.
- estimate retention volume class.
- detect protected water-intake and groundwater contexts.
- recommend blue-green infrastructure.

### 7.11. Geologia, osuwiska, górnictwo i grunty

**Pytania architekta:**

- Czy działka leży na osuwisku albo terenie zagrożonym ruchami masowymi?
- Czy występują obszary/tereny górnicze lub złoża?
- Jakie są wstępne warunki gruntowo-wodne?
- Czy spodziewany jest wysoki poziom wód gruntowych?
- Czy potrzebne są badania geotechniczne?
- Czy podpiwniczenie jest ryzykowne?

**Wymagane odpowiedzi/funkcje systemu:**

- intersect with SOPO, mining and geological layers.
- fetch CBDG/PIG context and borehole hints where available.
- score geotechnical risk.
- generate geotechnical investigation brief.
- flag basement and groundwater uncertainty.

### 7.12. Środowisko i ochrona przyrody

**Pytania architekta:**

- Czy działka leży w Natura 2000, parku, rezerwacie, OChK lub innej formie ochrony?
- Czy są pomniki przyrody, siedliska, gatunki chronione lub korytarze ekologiczne?
- Czy potrzebna będzie decyzja środowiskowa albo inwentaryzacja przyrodnicza?
- Czy wycinka drzew wymaga procedury?
- Czy planowana funkcja może znacząco oddziaływać na środowisko?

**Wymagane odpowiedzi/funkcje systemu:**

- intersect with GDOŚ/CRFOP and biodiversity layers.
- detect tree cover from orthophoto/LiDAR as precheck.
- run EIA screening by investment type.
- generate RDOŚ/environment questions.
- score environmental risk and mitigation actions.

### 7.13. Dziedzictwo, konserwator i archeologia

**Pytania architekta:**

- Czy działka lub obiekty są wpisane do rejestru zabytków?
- Czy działka leży w gminnej ewidencji zabytków albo strefie ochrony konserwatorskiej?
- Czy istnieje strefa archeologiczna?
- Czy rozbiórka, przebudowa, wysokość, materiały lub dach wymagają uzgodnienia?

**Wymagane odpowiedzi/funkcje systemu:**

- check NID and local heritage sources.
- extract conservation zones from plans.
- detect archaeology constraints.
- generate conservator checklist.
- flag demolition/adaptation risks.

### 7.14. Sąsiedztwo i kontekst urbanistyczny

**Pytania architekta:**

- Jaka zabudowa istnieje wokół?
- Jakie są wysokości i linie zabudowy sąsiadów?
- Czy sąsiedzi mają okna przy granicy?
- Czy są źródła hałasu, zapachów, ruchu ciężkiego albo uciążliwości?
- Czy otoczenie wspiera uzyskanie WZ?

**Wymagane odpowiedzi/funkcje systemu:**

- fetch and analyze nearby buildings, land use and road network.
- estimate heights from LiDAR/NMPT.
- infer frontage line and good-neighborhood context.
- detect nuisance sources.
- prepare WZ-neighborhood precheck.

### 7.15. Słońce, cień i mikroklimat

**Pytania architekta:**

- Jak najlepiej zorientować budynek?
- Gdzie lokować ogród, taras, część dzienną i PV?
- Czy drzewa lub sąsiedzi zacieniają działkę?
- Czy projektowana zabudowa może zacienić sąsiadów?
- Czy wiatr, hałas, upał lub retencja wpływają na koncepcję?

**Wymagane odpowiedzi/funkcje systemu:**

- compute sun path, shadows and seasonal exposure.
- score garden and PV orientation.
- run 2D/3D shadow scenarios.
- estimate privacy and overlooking risks.
- generate climate-resilient design notes.

### 7.16. Istniejące obiekty i stan terenu

**Pytania architekta:**

- Czy działka jest zabudowana?
- Czy budynki są ujawnione w danych urzędowych?
- Czy obiekty trzeba rozebrać lub można adaptować?
- Czy ortofoto pokazuje rozbieżności?
- Czy są drzewa, ogrodzenia, utwardzenia, fundamenty, odpady albo ślady wcześniejszego użytkowania?

**Wymagane odpowiedzi/funkcje systemu:**

- detect buildings from BDOT/EGiB/orthophoto/LiDAR.
- compare current and historical imagery.
- flag unregistered objects.
- generate demolition/adaptation checklist.
- estimate site-clearance cost class.

### 7.17. Procedury administracyjne

**Pytania architekta:**

- Czy potrzebne jest pozwolenie, zgłoszenie, WZ, decyzja środowiskowa, wodnoprawna, uzgodnienie zjazdu lub konserwatora?
- Jakie dokumenty trzeba przygotować?
- Kto jest organem właściwym?
- Czy wymagana jest mapa do celów projektowych, geotechnika, warunki przyłączenia?
- Czy potrzebne jest wyłączenie z produkcji rolnej/leśnej?

**Wymagane odpowiedzi/funkcje systemu:**

- select procedural pathway.
- generate document checklist and authority list.
- build critical path of decisions.
- detect manual expert dependencies.
- generate letters/questions to authorities and network operators.

### 7.18. Chłonność inwestycyjna

**Pytania architekta:**

- Ile PUM/GFA można uzyskać?
- Ile domów, mieszkań, lokali albo miejsc parkingowych zmieści się na działce?
- Który typ zabudowy jest najbardziej racjonalny?
- Czy działkę warto podzielić, scalić albo dokupić sąsiedni teren?
- Jaki wariant jest konserwatywny, optymistyczny i maksymalny?

**Wymagane odpowiedzi/funkcje systemu:**

- generate capacity scenarios by building type.
- compute footprint, GFA, PUM/PUU and parking.
- rank scenarios by risk and efficiency.
- run sensitivity analysis.
- detect subdivision/assembly potential.

### 7.19. Ryzyko i decyzja inwestycyjna

**Pytania architekta:**

- Co jest największym blockerem?
- Jakie ryzyka trzeba potwierdzić przed zakupem?
- Co może znacząco zwiększyć koszt?
- Czy działka jest lepsza od alternatyw?
- Jakie warunki zawrzeć w umowie przedwstępnej?

**Wymagane odpowiedzi/funkcje systemu:**

- build risk register and red flag list.
- score legal, planning, environmental, geotechnical, utility, road and cost risks.
- generate pre-purchase checklist.
- compare parcels in portfolio.
- recommend next best actions.

## 8. Pełny katalog funkcjonalności implementacyjnych

Nie wystawiać wszystkich pozycji jako osobnych narzędzi MCP. To jest katalog modułów wewnętrznych i wymagań. Publiczna powierzchnia MCP powinna być mniejsza, stabilna i dobrze opisana.

### 8.1. Wejście, identyfikacja i geometria

F-0001. obsługa pełnego identyfikatora działki.
F-0002. wyszukiwanie po numerze działki, obrębie i gminie.
F-0003. wyszukiwanie po punkcie XY.
F-0004. wyszukiwanie po adresie.
F-0005. import działki z GeoJSON.
F-0006. import działki z GML.
F-0007. import działki z SHP/ZIP.
F-0008. import działki z GeoPackage.
F-0009. import działki z KML/KMZ.
F-0010. import granicy z DXF/DWG.
F-0011. import PDF jako dokumentu źródłowego.
F-0012. import paczki ZIP due diligence.
F-0013. import listy działek CSV/XLSX.
F-0014. automatyczne wykrywanie CRS.
F-0015. transformacja do EPSG:2180.
F-0016. walidacja topologii.
F-0017. naprawa prostych błędów geometrii.
F-0018. oznaczanie geometrii niepewnej.
F-0019. normalizacja TERYT.
F-0020. normalizacja obrębu.
F-0021. detekcja geometrii wieloczęściowej.
F-0022. łączenie działek w teren inwestycji.
F-0023. podział analizy na działki składowe.
F-0024. wykrywanie granic administracyjnych.
F-0025. wykrywanie granic planów.
F-0026. hash inputu i snapshot.
F-0027. porównanie powierzchni źródłowej i obliczonej.
F-0028. detekcja rozbieżności granic.
F-0029. profil szerokości działki.
F-0030. detekcja frontu.
F-0031. detekcja działki narożnej.
F-0032. detekcja przewężeń.
F-0033. detekcja enklaw i klinów.
F-0034. oś główna działki.
F-0035. azymut frontu.
F-0036. klasyfikacja krawędzi granicy.
F-0037. eksport oczyszczonej geometrii.
F-0038. miniatura mapy lokalizacyjnej.
F-0039. tryb ręcznego override geometrii.
F-0040. audyt zmian geometrii.

### 8.2. Connectory i pobieranie danych

F-0041. connector ULDK.
F-0042. connector EGiB tam, gdzie usługi są dostępne.
F-0043. connector PRG.
F-0044. connector TERYT/GUS.
F-0045. connector Geoportal WMS.
F-0046. connector Geoportal WMTS.
F-0047. connector Geoportal WFS.
F-0048. connector Geoportal WCS.
F-0049. connector ortofotomapy.
F-0050. connector archiwalnej ortofotomapy.
F-0051. connector NMT.
F-0052. connector NMPT.
F-0053. connector LiDAR/LAZ.
F-0054. connector BDOT10k.
F-0055. connector BDOT500, jeśli dostępny.
F-0056. connector GESUT/KIUT.
F-0057. connector APP/GML.
F-0058. connector Przeglądarki Danych Planistycznych.
F-0059. connector Rejestru Urbanistycznego.
F-0060. connector lokalnego BIP.
F-0061. connector lokalnego SIP.
F-0062. adapter ArcGIS Server.
F-0063. adapter e-mapa.
F-0064. connector ISOK MZP/MRP/WORP.
F-0065. connector MPHP/hydrografii.
F-0066. connector GDOŚ/CRFOP.
F-0067. connector BDZP/siedliska.
F-0068. connector PIG SOPO.
F-0069. connector PIG CBDG.
F-0070. connector PIG MIDAS/ROG.
F-0071. connector NID.
F-0072. connector GDDKiA.
F-0073. connector danych kolejowych.
F-0074. connector danych lotniczych/stref wysokości.
F-0075. connector OSM jako pomocniczy.
F-0076. connector danych gestorów.
F-0077. connector danych rynkowych, jeśli legalnie dostępny.
F-0078. retry z backoff.
F-0079. circuit breaker.
F-0080. healthcheck źródeł.
F-0081. GetCapabilities introspection.
F-0082. automatyczne odkrywanie warstw.
F-0083. minimalizacja bbox.
F-0084. bulk fetch dla portfeli.
F-0085. cache WMS/WFS/WCS.
F-0086. cache GML APP.
F-0087. cache danych per gmina.
F-0088. cache kafli.
F-0089. snapshot źródła.
F-0090. wersjonowanie źródeł.
F-0091. monitoring zmian źródeł.
F-0092. walidacja licencji.
F-0093. oznaczanie danych poglądowych.
F-0094. oznaczanie braku pokrycia.
F-0095. mocki connectorów do CI.
F-0096. testy kontraktowe connectorów.

### 8.3. Planowanie przestrzenne i przepisy

F-0097. wykrycie pokrycia MPZP.
F-0098. wykrycie pokrycia POG.
F-0099. obsługa braku MPZP.
F-0100. obsługa WZ dostarczonej przez użytkownika.
F-0101. obsługa ULICP.
F-0102. obsługa ZPI.
F-0103. obsługa projektów planów.
F-0104. obsługa procedur planistycznych w toku.
F-0105. obsługa obszaru uzupełnienia zabudowy.
F-0106. obsługa lokalnych standardów urbanistycznych.
F-0107. obsługa obszarów rewitalizacji.
F-0108. pobranie GML APP.
F-0109. walidacja GML APP.
F-0110. przecięcie stref planistycznych z działką.
F-0111. rozpoznanie symbolu terenu.
F-0112. parser uchwały MPZP.
F-0113. parser ustaleń ogólnych.
F-0114. parser ustaleń szczegółowych.
F-0115. parser definicji lokalnych.
F-0116. parser wskaźników parkingowych.
F-0117. parser linii zabudowy.
F-0118. parser zakazów i nakazów.
F-0119. parser geometrii dachu.
F-0120. parser materiałów i kolorystyki.
F-0121. macierz funkcji podstawowych.
F-0122. macierz funkcji dopuszczalnych.
F-0123. macierz funkcji zakazanych.
F-0124. wykrywanie konfliktów między rysunkiem a tekstem.
F-0125. wykrywanie konfliktów między GML a PDF.
F-0126. ocena stabilności planistycznej.
F-0127. trace cytatów z planu.
F-0128. ruleset dla WT.
F-0129. ruleset dla planowania.
F-0130. ruleset dla ochrony środowiska.
F-0131. ruleset dla parkingów.
F-0132. ruleset dla stref technicznych.
F-0133. wersjonowanie reguł.
F-0134. tryb strict legal source only.
F-0135. tryb conservative assumptions.
F-0136. tryb optimistic assumptions.
F-0137. ręczny override eksperta.
F-0138. audyt override.
F-0139. testy regresyjne reguł.

### 8.4. Buildable envelope i analizy projektowe

F-0140. obliczenie odległości od granic.
F-0141. obliczenie wariantów ścian z oknami.
F-0142. obliczenie wariantów ścian bez okien.
F-0143. obliczenie możliwości zabudowy przy granicy.
F-0144. import cyfrowych linii zabudowy.
F-0145. wektoryzacja linii z rysunku planu jako low-confidence.
F-0146. bufory od dróg.
F-0147. bufory od kolei.
F-0148. bufory od lasów.
F-0149. bufory od cieków.
F-0150. bufory od sieci.
F-0151. bufory od linii energetycznych.
F-0152. bufory od stref ochronnych.
F-0153. bufory od zabytków.
F-0154. bufory od skarp.
F-0155. bufory od wałów.
F-0156. bufory od infrastruktury technicznej.
F-0157. wyznaczenie no-build zones.
F-0158. wyznaczenie buildable polygon.
F-0159. największy prostokąt wpisany.
F-0160. największy wielobok ortogonalny.
F-0161. warianty footprintu.
F-0162. warianty lokalizacji domu.
F-0163. warianty bliźniaka.
F-0164. warianty szeregowca.
F-0165. warianty wielorodzinne.
F-0166. warianty usługowe.
F-0167. warianty magazynowe.
F-0168. warianty garażu.
F-0169. layout parkingów.
F-0170. layout dojść pieszych.
F-0171. layout dojazdu pożarowego.
F-0172. layout śmietnika.
F-0173. layout retencji.
F-0174. layout zieleni.
F-0175. layout placu zabaw.
F-0176. analiza prywatności.
F-0177. analiza widoków.
F-0178. ranking wariantów.
F-0179. eksport wariantów do GIS/CAD/BIM.

### 8.5. Chłonność i parametry inwestycyjne

F-0180. maksymalna powierzchnia zabudowy.
F-0181. maksymalna GFA.
F-0182. szacunkowy PUM.
F-0183. szacunkowy PUU.
F-0184. minimalna PBC.
F-0185. wymagane parkingi samochodowe.
F-0186. wymagane parkingi rowerowe.
F-0187. limit kondygnacji.
F-0188. limit wysokości.
F-0189. limit długości elewacji.
F-0190. limit szerokości elewacji.
F-0191. limit intensywności minimalnej.
F-0192. limit intensywności maksymalnej.
F-0193. bilans powierzchni utwardzonych.
F-0194. bilans zieleni.
F-0195. bilans retencji.
F-0196. program fit dla domu jednorodzinnego.
F-0197. program fit dla dewelopera jednorodzinnego.
F-0198. program fit dla wielorodzinnego.
F-0199. program fit dla usług.
F-0200. program fit dla magazynu.
F-0201. program fit dla produkcji.
F-0202. wariant konserwatywny.
F-0203. wariant bazowy.
F-0204. wariant optymistyczny.
F-0205. wariant maksymalny.
F-0206. analiza wrażliwości.
F-0207. potencjał podziału.
F-0208. potencjał scalenia.
F-0209. potencjał dokupu sąsiedniej działki.
F-0210. współczynnik wykorzystania buildable envelope.
F-0211. porównanie efektywności wariantów.
F-0212. szacunkowy koszt czynników terenowych.
F-0213. szacunkowy koszt kolizji.
F-0214. szacunkowy koszt przyłączy.
F-0215. klasyfikacja opłacalności jako precheck.

### 8.6. Drogi, transport i dostęp

F-0216. detekcja drogi publicznej.
F-0217. detekcja drogi wewnętrznej.
F-0218. łańcuch dostępu przez działki drogowe.
F-0219. wymóg służebności.
F-0220. długość styku z drogą.
F-0221. szerokość frontu przy drodze.
F-0222. klasa drogi.
F-0223. zarządca drogi.
F-0224. istniejący zjazd.
F-0225. możliwość nowego zjazdu.
F-0226. ryzyko odmowy zjazdu.
F-0227. widoczność na zjeździe jako precheck.
F-0228. geometria skrętu.
F-0229. dojazd budowy.
F-0230. dojazd ppoż..
F-0231. dojazd śmieciarki.
F-0232. dostawy i logistyka.
F-0233. rezerwa pod drogę.
F-0234. planowane drogi.
F-0235. poszerzenie drogi.
F-0236. kolizja z pasem drogowym.
F-0237. odległość do przystanku.
F-0238. odległość do kolei.
F-0239. odległość do drogi wyższej klasy.
F-0240. ryzyko hałasu drogowego.
F-0241. pytania do zarządcy drogi.

### 8.7. Media i infrastruktura techniczna

F-0242. wykrycie sieci elektroenergetycznej.
F-0243. wykrycie sieci wodociągowej.
F-0244. wykrycie kanalizacji sanitarnej.
F-0245. wykrycie kanalizacji deszczowej.
F-0246. wykrycie gazu.
F-0247. wykrycie ciepła.
F-0248. wykrycie telekomunikacji.
F-0249. wykrycie światłowodu.
F-0250. wykrycie hydrantów.
F-0251. wykrycie stacji transformatorowych.
F-0252. wykrycie studni.
F-0253. wykrycie rowów i drenażu.
F-0254. najbliższa sieć poza działką.
F-0255. sieci przecinające działkę.
F-0256. sieci przecinające buildable envelope.
F-0257. strefy techniczne.
F-0258. kolizje z fundamentami.
F-0259. kolizje z dojazdem.
F-0260. kolizje z drzewami.
F-0261. ocena kosztu przebudowy.
F-0262. brak danych o przepustowości.
F-0263. pytania do gestorów.
F-0264. wariant studni.
F-0265. wariant szamba.
F-0266. wariant oczyszczalni.
F-0267. wariant retencji.
F-0268. wariant OZE.
F-0269. wariant pompy ciepła.
F-0270. wariant braku gazu.

### 8.8. Teren, wody i geologia

F-0271. profil wysokościowy.
F-0272. mapa spadków.
F-0273. mapa ekspozycji.
F-0274. warstwice.
F-0275. lokalne depresje.
F-0276. kierunek spływu.
F-0277. mikrozlewnia.
F-0278. bilans mas ziemnych.
F-0279. ryzyko murów oporowych.
F-0280. ryzyko osuwiska.
F-0281. ryzyko ruchów masowych.
F-0282. obszar górniczy.
F-0283. teren górniczy.
F-0284. złoże kopalin.
F-0285. otwory wiertnicze w pobliżu.
F-0286. wstępne warunki gruntowe.
F-0287. wody gruntowe jako ryzyko.
F-0288. GZWP.
F-0289. strefy ochrony ujęć.
F-0290. mapy zagrożenia powodziowego.
F-0291. mapy ryzyka powodziowego.
F-0292. WORP.
F-0293. odległość do cieku.
F-0294. odległość do rowu.
F-0295. odległość do zbiornika.
F-0296. wały przeciwpowodziowe.
F-0297. podtopienia jako hint.
F-0298. pozwolenie wodnoprawne precheck.
F-0299. retencja precheck.
F-0300. infiltracja precheck.
F-0301. garaż podziemny precheck.
F-0302. podpiwniczenie precheck.
F-0303. brief dla geotechnika.

### 8.9. Środowisko, zabytki i ograniczenia specjalne

F-0304. Natura 2000.
F-0305. park narodowy.
F-0306. park krajobrazowy.
F-0307. rezerwat.
F-0308. obszar chronionego krajobrazu.
F-0309. użytek ekologiczny.
F-0310. pomnik przyrody.
F-0311. zespół przyrodniczo-krajobrazowy.
F-0312. stanowiska gatunków.
F-0313. siedliska.
F-0314. korytarze ekologiczne.
F-0315. decyzja środowiskowa precheck.
F-0316. inwentaryzacja przyrodnicza.
F-0317. wycinka drzew precheck.
F-0318. kompensacja przyrodnicza jako ryzyko.
F-0319. rejestr zabytków.
F-0320. gminna ewidencja zabytków.
F-0321. strefa konserwatorska.
F-0322. strefa archeologiczna.
F-0323. układ urbanistyczny.
F-0324. rozbiórka zabytku.
F-0325. uzgodnienie konserwatora.
F-0326. ograniczenia materiałowe.
F-0327. ograniczenia dachów.
F-0328. ograniczenia wysokości.
F-0329. ochrona krajobrazu.
F-0330. strefy lotnicze.
F-0331. linie wysokiego napięcia.
F-0332. farmy wiatrowe jako sąsiedztwo.
F-0333. cmentarze i strefy sanitarne.
F-0334. zakłady przemysłowe i uciążliwości.

### 8.10. Sąsiedztwo, klimat i jakość życia

F-0335. budynki sąsiednie.
F-0336. wysokości z LiDAR.
F-0337. linia zabudowy sąsiadów.
F-0338. funkcje sąsiednie.
F-0339. dobrego sąsiedztwa WZ precheck.
F-0340. okna przy granicy.
F-0341. zacienianie od sąsiadów.
F-0342. zacienianie sąsiadów.
F-0343. nasłonecznienie.
F-0344. ścieżka słońca.
F-0345. potencjał PV.
F-0346. orientacja ogrodu.
F-0347. prywatność.
F-0348. widoki.
F-0349. hałas drogowy.
F-0350. hałas kolejowy.
F-0351. hałas przemysłowy.
F-0352. zapachy.
F-0353. ruch ciężki.
F-0354. zapylenie.
F-0355. heat island.
F-0356. retencja krajobrazowa.
F-0357. przewietrzanie.
F-0358. dominujące wiatry.
F-0359. zieleń istniejąca.
F-0360. drzewa z LiDAR.
F-0361. drzewa z ortofoto.
F-0362. zmiany historyczne terenu.

### 8.11. Procedury, dokumenty i korespondencja

F-0363. ścieżka pozwolenie vs zgłoszenie.
F-0364. ścieżka WZ.
F-0365. ścieżka MPZP.
F-0366. ścieżka POG.
F-0367. ścieżka decyzja środowiskowa.
F-0368. ścieżka wodnoprawna.
F-0369. ścieżka zjazdu.
F-0370. ścieżka konserwatorska.
F-0371. ścieżka odrolnienia.
F-0372. ścieżka wyłączenia z produkcji leśnej.
F-0373. mapa do celów projektowych checklist.
F-0374. geotechnika checklist.
F-0375. warunki przyłączenia checklist.
F-0376. opinie i uzgodnienia checklist.
F-0377. brief do urzędu.
F-0378. brief do gestorów.
F-0379. brief do geodety.
F-0380. brief do geotechnika.
F-0381. brief do projektanta drogowego.
F-0382. brief do prawnika.
F-0383. brief dla sprzedającego.
F-0384. warunki umowy przedwstępnej.
F-0385. lista dokumentów minimalna.
F-0386. lista dokumentów pełna.
F-0387. krytyczna ścieżka decyzji.
F-0388. status manual review.

### 8.12. Raporty, eksport i UX

F-0389. raport Markdown.
F-0390. raport HTML.
F-0391. raport PDF.
F-0392. raport JSON.
F-0393. raport GeoPackage.
F-0394. warstwy DXF.
F-0395. warstwy SVG.
F-0396. mapy PNG.
F-0397. mapa interaktywna.
F-0398. tabela parametrów.
F-0399. tabela ograniczeń.
F-0400. tabela źródeł.
F-0401. tabela ryzyk.
F-0402. executive summary.
F-0403. raport dla architekta.
F-0404. raport dla inwestora.
F-0405. raport dla prawnika.
F-0406. raport dla banku/funduszu.
F-0407. evidence pack.
F-0408. confidence explanation.
F-0409. why-this-result.
F-0410. lista red flags.
F-0411. lista next actions.
F-0412. lista unknowns.
F-0413. porównanie raportów.
F-0414. wersjonowanie raportów.
F-0415. komentarze.
F-0416. adnotacje mapowe.
F-0417. share link.
F-0418. webhook alertów.
F-0419. CSV/XLSX export.
F-0420. API result package.

### 8.13. Autonomia, orkiestracja i agent workflows

F-0421. autonomiczny plan analizy.
F-0422. wybór źródeł po lokalizacji.
F-0423. fallback między źródłami.
F-0424. degradacja wyniku przy awarii.
F-0425. automatyczna ocena confidence.
F-0426. automatyczne wykrycie braków.
F-0427. automatyczna lista pytań do człowieka.
F-0428. kolejka zadań.
F-0429. priorytetyzacja red flags.
F-0430. wznawianie analizy.
F-0431. idempotencja narzędzi.
F-0432. cache-aware planning.
F-0433. chunkowanie dużych wyników.
F-0434. streaming statusu.
F-0435. task graph.
F-0436. dependency graph.
F-0437. manual-review gates.
F-0438. agent memory per analysis.
F-0439. source freshness monitor.
F-0440. ruleset freshness monitor.
F-0441. autotest source connectors.
F-0442. self-diagnostics.
F-0443. safe failure mode.
F-0444. no hallucinated legal conclusions.
F-0445. conservative default.
F-0446. audit trail.

### 8.14. MCP, API i integracje deweloperskie

F-0447. MCP stdio transport dla Claude Code.
F-0448. Streamable HTTP dla serwera zdalnego.
F-0449. opcjonalny SSE legacy.
F-0450. MCP tools.
F-0451. MCP resources.
F-0452. MCP prompts.
F-0453. resource templates.
F-0454. structuredContent.
F-0455. progress notifications.
F-0456. logging.
F-0457. tool result pagination.
F-0458. artifact resources.
F-0459. source resources.
F-0460. report resources.
F-0461. map resources.
F-0462. schema resources.
F-0463. OpenAPI HTTP API.
F-0464. CLI.
F-0465. Python SDK client.
F-0466. TypeScript SDK client.
F-0467. Docker image.
F-0468. docker-compose dev.
F-0469. Kubernetes deployment.
F-0470. PostGIS migrations.
F-0471. background workers.
F-0472. object storage.
F-0473. message queue.
F-0474. admin panel.
F-0475. health endpoint.
F-0476. metrics endpoint.

### 8.15. Bezpieczeństwo, compliance i audyt

F-0477. brak sekretów w repo.
F-0478. zarządzanie sekretami.
F-0479. RBAC.
F-0480. tenant isolation.
F-0481. audit log.
F-0482. PII classification.
F-0483. maskowanie danych właścicielskich.
F-0484. retencja danych.
F-0485. szyfrowanie w spoczynku.
F-0486. szyfrowanie w transporcie.
F-0487. rate limiting.
F-0488. egress allowlist.
F-0489. SSRF protection.
F-0490. path traversal protection.
F-0491. sandbox plików użytkownika.
F-0492. antywirus dla uploadów.
F-0493. limity rozmiaru plików.
F-0494. OCR tylko kontrolowany.
F-0495. prompt injection defense.
F-0496. tool allowlist.
F-0497. command allowlist.
F-0498. safe filesystem boundaries.
F-0499. signed artifacts.
F-0500. supply-chain scanning.
F-0501. SBOM.
F-0502. license compliance.
F-0503. privacy by design.

### 8.16. Wydajność, skalowanie i niezawodność

F-0504. PostGIS spatial indexes.
F-0505. materialized overlays.
F-0506. precomputed municipal caches.
F-0507. vector tile cache.
F-0508. raster tile cache.
F-0509. in-memory LRU.
F-0510. parallel IO.
F-0511. async connectors.
F-0512. batching requests.
F-0513. bbox minimization.
F-0514. geometry simplification.
F-0515. multi-resolution geometry.
F-0516. lazy loading evidence.
F-0517. quick mode under strict budget.
F-0518. full mode resumable.
F-0519. portfolio queue.
F-0520. worker autoscaling.
F-0521. cache invalidation by source version.
F-0522. cold-start cache warming.
F-0523. circuit breakers.
F-0524. partial results.
F-0525. timeout budgets.
F-0526. observability traces.
F-0527. error budgets.
F-0528. benchmark suite.
F-0529. load tests.
F-0530. profiling.
F-0531. slow query log.
F-0532. database partitioning.
F-0533. read replicas.
F-0534. object storage lifecycle.
F-0535. deterministic retries.
F-0536. backpressure.

### 8.17. Testy i jakość

F-0537. unit tests geometrii.
F-0538. unit tests reguł.
F-0539. contract tests connectorów.
F-0540. integration tests PostGIS.
F-0541. golden parcels.
F-0542. golden MPZP cases.
F-0543. golden flood cases.
F-0544. golden protected-area cases.
F-0545. golden heritage cases.
F-0546. snapshot tests raportów.
F-0547. property-based tests geometrii.
F-0548. CRS transformation tests.
F-0549. parser evaluation set.
F-0550. LLM parser hallucination tests.
F-0551. evidence completeness tests.
F-0552. confidence calibration tests.
F-0553. performance benchmarks.
F-0554. load tests.
F-0555. security tests.
F-0556. upload fuzzing.
F-0557. MCP protocol tests.
F-0558. API schema tests.
F-0559. regression suite per ruleset.
F-0560. manual expert review set.
F-0561. acceptance tests by use case.

---

## 9. Architektura systemu

### 9.1. Zalecany podział na komponenty

```text
plot-analyzer/
  apps/
    api/                         # HTTP API, auth, job orchestration
    mcp-server/                  # serwer MCP dla Claude Code
    worker/                      # kolejka analiz, batch, cache warming
    web/                         # opcjonalny panel/mapa
  packages/
    domain/                      # encje, value objects, scoring, use-cases
    geo/                         # CRS, PostGIS, overlays, rastry, wektory
    connectors/                  # GUGiK, APP, ISOK, GDOŚ, PIG, NID, BIP, pliki
    planning/                    # parsery MPZP/POG/WZ i model ustaleń
    rules/                       # engine reguł i wersjonowane rulesety
    envelope/                    # buildable envelope i warianty footprintów
    reports/                     # Markdown/HTML/PDF/JSON/GPKG/DXF
    evidence/                    # źródła, trace, confidence
    agent/                       # planner autonomiczny, task graph, fallback
    security/                    # upload sandbox, policy, redaction
    shared/                      # typy, logowanie, konfiguracja
  rulesets/
    PL/
      building-technical/
      planning/
      environmental/
      utilities/
      road-access/
  schemas/
    openapi.yaml
    mcp-tools.schema.json
    analysis-result.schema.json
  tests/
    golden-parcels/
    fixtures/
    integration/
    performance/
  infra/
    docker-compose.yml
    k8s/
    terraform/
```

### 9.2. Warstwy architektoniczne

1. **Input layer** — walidacja danych wejściowych, CRS, identyfikatorów i plików.
2. **Source layer** — connectory, retry, cache, healthcheck, licencje, snapshoty.
3. **Canonical data layer** — PostGIS, obiekty domenowe, normalizacja, evidence records.
4. **Analysis layer** — overlay, buforowanie, buildable envelope, teren, media, ryzyka.
5. **Rules layer** — wersjonowane reguły i trace decyzji.
6. **Agent orchestration layer** — task graph, fallback, partial results, confidence.
7. **Presentation layer** — raporty, mapy, eksporty, MCP resources.
8. **Integration layer** — MCP, HTTP API, CLI, SDK.

### 9.3. Zalecany stos technologiczny

Wariant preferowany:

- Backend: Python 3.12+ albo TypeScript/Node.js; wybór utrzymać konsekwentnie dla całego serwera MCP.
- GIS: PostGIS, GDAL/OGR, PROJ, Shapely/GEOS, Rasterio, PyProj, Tippecanoe/tileserver opcjonalnie.
- Queue: Redis/RQ, Celery, Dramatiq albo BullMQ.
- Cache: Redis + PostGIS materialized tables + object storage.
- Storage: PostgreSQL/PostGIS + S3-compatible object storage.
- Reports: Markdown/HTML first, PDF as renderer, GeoPackage/DXF jako export przestrzenny.
- Observability: OpenTelemetry, Prometheus metrics, structured logs.
- Deployment: Docker, docker-compose dev, Kubernetes opcjonalnie.
- Rulesets: YAML/JSON z walidacją JSON Schema.

### 9.4. Zasady kodowania

- Nie mieszać kodu connectorów z regułami domenowymi.
- Nie hardkodować stałych prawnych bez wersji, źródła i daty obowiązywania.
- Każdy moduł analityczny ma zwracać: `result`, `evidence`, `confidence`, `warnings`, `unknowns`.
- Każde narzędzie MCP ma być idempotentne, chyba że jawnie tworzy zasób/snapshot.
- Każde publiczne API musi mieć stabilny schemat i test kontraktowy.
- Wszystkie operacje geometrii wykonywać w ustalonym CRS roboczym; wynik mapowy może być transformowany do innych CRS.
- Każda analiza ma `analysis_id`, `input_hash`, `source_snapshot_id`, `ruleset_version`, `created_at`.
- Parsery LLM nie mogą być jedynym źródłem prawdy. Wynik parsera musi być walidowany przez reguły, schematy i evidence.
- Dla operacji niepewnych stosować „confidence-first UX”.

---

## 10. Serwer MCP

### 10.1. Cel MCP

Serwer MCP ma umożliwić Claude Code wykonywanie analiz działek, pobieranie kontekstu, przeglądanie evidence pack, generowanie raportów i sterowanie workflow bez ręcznego klikania w aplikację. MCP ma dawać modelowi narzędzia wykonawcze oraz zasoby do czytania, ale nie powinien eksponować setek drobnych funkcji jako osobnych tools, bo to zwiększa koszt kontekstu i pogarsza sterowalność.

### 10.2. Transporty

- `stdio` — podstawowy transport dla lokalnego Claude Code.
- `streamable_http` — transport dla wdrożeń serwerowych i zespołowych.
- `sse` — tylko jako kompatybilność wsteczna, jeśli wymagana.

### 10.3. Publiczne narzędzia MCP

Minimalna publiczna powierzchnia narzędzi:

```yaml
tools:
  parcel_resolve:
    description: Resolve parcel from id, address, point, geometry or uploaded file.
  parcel_analyze:
    description: Run quick/full/design/portfolio analysis.
  analysis_get_status:
    description: Return status, progress and partial results.
  analysis_get_result:
    description: Return structured result for an analysis run.
  planning_fetch:
    description: Fetch planning context and planning acts for parcel/area.
  planning_parse_document:
    description: Parse user-supplied planning document with evidence.
  constraints_compute:
    description: Compute constraints and buildable envelope.
  capacity_generate_scenarios:
    description: Generate building capacity scenarios.
  risks_list:
    description: Return red flags, risk register and unknowns.
  sources_collect:
    description: Collect source records and evidence pack.
  report_generate:
    description: Generate report artifact in selected format.
  export_layers:
    description: Export GIS/CAD layers.
  portfolio_analyze:
    description: Analyze many parcels.
  monitoring_create:
    description: Create monitoring profile for changes.
  ruleset_explain:
    description: Explain which rules were applied.
  source_healthcheck:
    description: Check external source availability.
  cache_warm:
    description: Preload source/cache data for municipality or parcel.
  document_ingest:
    description: Ingest user documents and attach to analysis.
  manual_override:
    description: Apply expert override with audit trail.
  diagnostics_run:
    description: Run diagnostics for debugging and QA.
```

### 10.4. Zasoby MCP

Zasoby muszą być czytelne i stabilne. Przykładowe URI:

```text
parcel://PL/{teryt}/{district}/{parcel_id}
analysis://{analysis_id}/summary
analysis://{analysis_id}/result.json
analysis://{analysis_id}/evidence
analysis://{analysis_id}/risks
analysis://{analysis_id}/unknowns
analysis://{analysis_id}/buildable-envelope.geojson
analysis://{analysis_id}/report.md
analysis://{analysis_id}/map-preview.png
planning://{municipality_id}/acts
planning://{municipality_id}/act/{act_id}
ruleset://PL/{ruleset_version}
source://{source_id}/metadata
schema://analysis-result
schema://risk-register
schema://mcp-tools
cache://health
```

### 10.5. Prompty MCP

Prompty powinny być gotowymi workflow dla Claude Code:

- `analyze_plot_for_purchase` — due diligence przed zakupem.
- `analyze_plot_for_single_family_house` — dom jednorodzinny.
- `analyze_plot_for_multifamily` — zabudowa wielorodzinna.
- `analyze_plot_for_services` — usługi/handel.
- `compare_parcels` — porównanie działek.
- `prepare_questions_for_office` — pytania do gminy/starostwa.
- `prepare_questions_for_network_operators` — pytania do gestorów.
- `prepare_architect_brief` — brief do koncepcji.
- `review_uploaded_planning_document` — analiza dostarczonego MPZP/WZ/wyrysu.
- `explain_red_flags` — wyjaśnienie ryzyk inwestorowi nietechnicznemu.

### 10.6. Schemat wejścia dla `parcel_analyze`

```json
{
  "input": {
    "parcel_id": "string | null",
    "address": "string | null",
    "point": {"x": 0, "y": 0, "crs": "EPSG:4326"},
    "geometry": "GeoJSON | WKT | null",
    "uploaded_files": ["file_id"]
  },
  "analysis_mode": "quick_screening | full_due_diligence | design_feasibility | portfolio_batch",
  "investment_goal": {
    "type": "single_family | multifamily | services | warehouse | mixed | unknown",
    "target_gfa_m2": "number | null",
    "target_units": "number | null",
    "risk_preference": "conservative | balanced | optimistic"
  },
  "options": {
    "country": "PL",
    "ruleset_version": "latest",
    "strict_sources_only": false,
    "include_auxiliary_sources": true,
    "return_maps": true,
    "return_evidence": true,
    "max_runtime_profile": "fast | standard | exhaustive"
  }
}
```

### 10.7. Schemat wyniku analizy

```json
{
  "analysis_id": "uuid",
  "status": "complete | partial | failed | manual_review_required",
  "decision": "OK | OK_WITH_RISKS | NEEDS_MANUAL_REVIEW | LIKELY_BLOCKED",
  "scores": {
    "buildability": 0,
    "planning_certainty": 0,
    "infrastructure": 0,
    "terrain": 0,
    "environmental_risk": 0,
    "procedural_risk": 0,
    "data_confidence": 0
  },
  "parcel": {},
  "planning": {},
  "constraints": [],
  "buildable_envelope": {},
  "capacity_scenarios": [],
  "risks": [],
  "unknowns": [],
  "next_actions": [],
  "evidence": [],
  "artifacts": []
}
```

---

## 11. Model danych domenowych

### 11.1. Encje główne

- `Parcel` — działka ewidencyjna.
- `InvestmentArea` — jedna lub wiele działek.
- `AdministrativeContext` — gmina, powiat, województwo, TERYT, obręb.
- `AnalysisRun` — uruchomienie analizy.
- `SourceRecord` — źródło danych.
- `EvidenceItem` — pojedynczy dowód.
- `PlanningAct` — MPZP, POG, WZ, ULICP, ZPI, projekt planu.
- `PlanningZone` — jednostka planistyczna lub strefa.
- `PlanningIndicator` — wskaźnik planistyczny.
- `Constraint` — ograniczenie geometryczne lub opisowe.
- `NoBuildZone` — obszar wyłączony.
- `BuildableEnvelope` — wynikowy obszar możliwej zabudowy.
- `CapacityScenario` — wariant chłonności.
- `UtilityNetwork` — sieć uzbrojenia.
- `RoadAccess` — dostęp drogowy.
- `TerrainModel` — NMT/NMPT i pochodne.
- `RiskItem` — ryzyko.
- `UnknownItem` — rzecz nieustalona.
- `Recommendation` — rekomendacja.
- `ReportArtifact` — raport/mapa/eksport.
- `Ruleset` — wersja reguł.
- `Override` — ręczna korekta eksperta.

### 11.2. Typy ryzyk

```yaml
risk_type:
  - planning
  - legal
  - ownership
  - road_access
  - utilities
  - environmental
  - flood
  - geology
  - heritage
  - terrain
  - technical_building_rules
  - procedural
  - cost
  - data_quality
  - market
severity:
  - info
  - low
  - medium
  - high
  - critical
confidence:
  - low
  - medium
  - high
status:
  - detected
  - suspected
  - not_detected
  - unknown
  - manual_review_required
```

### 11.3. Minimalny rekord ograniczenia

```yaml
constraint_id: string
constraint_type: string
source_id: string
source_legal_status: binding | informative | auxiliary | unknown
geometry: GeoJSON | null
applies_to_area_m2: number | null
applies_to_percent: number | null
rule_id: string | null
rule_version: string | null
severity: info | low | medium | high | critical
confidence: number
human_summary: string
machine_summary: object
mitigation: string | null
```

---

## 12. Silnik reguł

### 12.1. Zasady

- Reguły muszą być deklaratywne, testowalne i wersjonowane.
- Każda reguła ma `id`, `title`, `jurisdiction`, `valid_from`, `valid_to`, `source_reference`, `inputs`, `outputs`, `severity`, `confidence_policy`.
- Reguły nie mogą zakładać, że dane są kompletne.
- Wynik reguły ma rozróżniać `pass`, `fail`, `warning`, `unknown`, `not_applicable`.
- Dla każdej reguły należy zapisać trace.

### 12.2. Przykładowy format reguły

```yaml
id: PL-WT-SETBACK-GRANICA-001
title: Minimalna odległość budynku od granicy działki
jurisdiction: PL
valid_from: 2024-01-01
valid_to: null
source_reference: "rulesets/PL/building-technical/sources.md#..."
inputs:
  - building_wall_type
  - parcel_boundary
  - planning_override
  - neighboring_conditions
outputs:
  - required_setback_m
  - applicability
  - confidence
logic: declarative_expression_or_function_ref
```

### 12.3. Kategorie rulesetów

- `PL/building-technical` — wymagania techniczno-budowlane jako precheck.
- `PL/planning` — interpretacja MPZP/POG/WZ.
- `PL/road-access` — dostęp do drogi, zjazdy, dojazd pożarowy jako precheck.
- `PL/utilities` — strefy techniczne i kolizje sieci.
- `PL/environmental` — formy ochrony, EIA, drzewa.
- `PL/water` — powódź, retencja, wodnoprawne.
- `PL/geology` — osuwiska, górnictwo, geotechnika.
- `PL/heritage` — konserwator, zabytki, archeologia.
- `PL/reporting` — reguły klasyfikacji ryzyk i raportowania.

---

## 13. Algorytmy przestrzenne

Wymagane algorytmy i metody:

- transformacja CRS i walidacja geometrii;
- overlay: intersection, union, difference, symmetric difference;
- buffering z poprawną jednostką metryczną;
- dissolving i polygonization;
- simplification z zachowaniem topologii;
- nearest-neighbor i distance-to-layer;
- clipping warstw do działki i bufora analizy;
- detekcja frontu względem drogi;
- profil szerokości działki;
- largest inscribed rectangle/polygon;
- skeleton/medial axis do analizy wąskich działek;
- raster sampling DEM/NMT/NMPT;
- slope, aspect, contours, hydrological flow direction;
- shadow casting 2D/3D;
- obstruction analysis dla sąsiedztwa;
- scoring ważony przez confidence;
- sensitivity analysis dla niepewnych parametrów;
- map generalization dla szybkiego podglądu;
- batch overlay z indeksami przestrzennymi.

---

## 14. Scoring

### 14.1. Wyniki główne

- `buildability_score` — możliwość zabudowy po ograniczeniach.
- `planning_certainty_score` — pewność planistyczna.
- `infrastructure_score` — dostęp do mediów i drogi.
- `terrain_score` — topografia i warunki terenowe.
- `environmental_risk_score` — ryzyka środowiskowe.
- `geotechnical_risk_score` — geologia i grunty.
- `heritage_risk_score` — zabytki i konserwator.
- `procedural_risk_score` — decyzje, uzgodnienia, ścieżka administracyjna.
- `cost_driver_score` — potencjalne koszty dodatkowe.
- `data_confidence_score` — jakość danych.
- `investment_fit_score` — zgodność z celem inwestora.

### 14.2. Zasady scoringu

- Score ma być wyjaśnialny. Każda składowa musi wskazywać czynniki dodatnie i ujemne.
- Nie sumować bezrefleksyjnie score, gdy występuje hard blocker. Hard blocker musi dominować rekomendację.
- Confidence wpływa na score: niska pewność zwiększa ryzyko proceduralne/danych.
- W raporcie pokazać score liczbowy i opisowy.
- Score nie jest decyzją prawną. To narzędzie porównawcze i priorytetyzujące.

---

## 15. Wydajność i autonomiczność

### 15.1. Wymagania wydajnościowe

- Quick screening musi działać z cache i minimalnym zestawem źródeł.
- Full analysis ma być wznawialna i częściowa; awaria jednego źródła nie blokuje całości.
- Wszystkie zapytania przestrzenne wykonywać przez PostGIS i indeksy GiST/SP-GiST.
- Dla gmin/powiatów utrzymywać materialized overlays najczęściej używanych warstw.
- Warstwy rasterowe próbkować tylko w bbox działki + bufor.
- Dane GML planistyczne cache'ować per gmina i wersja aktu.
- Wyniki długie paginować/chunkować.
- Zasoby MCP nie powinny ładować całych map i evidence do kontekstu; model ma pobierać zasoby na żądanie.
- W portfelach używać batch processing, deduplikacji źródeł i cache warming.
- Każdy connector ma timeout budget, retry budget i circuit breaker.

### 15.2. Wymagania autonomiczności

- Agent sam rozpoznaje, które źródła są potrzebne.
- Agent sam wykrywa brak MPZP i przełącza się na POG/WZ/BIP/manual review.
- Agent sam wykrywa niepełne dane GESUT/EGiB i oznacza wynik jako niepewny.
- Agent sam generuje listę brakujących dokumentów.
- Agent sam uruchamia alternatywne ścieżki, np. PDF planu, GML APP, lokalny SIP.
- Agent sam priorytetyzuje czerwone flagi przed analizą detali.
- Agent sam zwraca partial result, gdy analiza pełna nie jest gotowa albo źródło nie odpowiada.
- Agent sam zapisuje evidence i nie polega na pamięci LLM.
- Agent sam wykrywa sprzeczności między źródłami i prosi o manual review.
- Agent sam proponuje następne kroki projektowe i administracyjne.

---

## 16. Bezpieczeństwo

Wymagania:

- Nigdy nie wykonywać komend systemowych z inputu użytkownika.
- Sandbox dla plików użytkownika.
- Ograniczenie typów plików i rozmiarów.
- Skanowanie plików uploadowanych.
- Ochrona przed path traversal.
- Ochrona przed SSRF w connectorach.
- Allowlista domen publicznych connectorów.
- Zarządzanie sekretami przez vault/env, nie przez repo.
- RBAC i separacja tenantów.
- Audyt dostępu do analiz i dokumentów.
- Szyfrowanie danych w tranzycie i spoczynku.
- Redakcja danych osobowych w raportach współdzielonych.
- Logowanie bez danych wrażliwych.
- Ochrona przed prompt injection z dokumentów PDF, BIP i stron HTML.
- LLM parser musi działać w trybie „untrusted content”.
- Narzędzia MCP wykonujące operacje zapisu muszą wymagać jawnego parametru celu i zapisywać audit log.
- Lokalne MCP przez stdio musi mieć ograniczony dostęp do filesystemu.

---

## 17. Raportowanie

Każdy raport ma zawierać:

1. Dane wejściowe i zakres analizy.
2. Identyfikację działki.
3. Mapę lokalizacyjną.
4. Parametry geometrii.
5. Kontekst administracyjny.
6. Status planistyczny.
7. Przeznaczenie i wskaźniki.
8. Ograniczenia i no-build zones.
9. Buildable envelope.
10. Chłonność i warianty.
11. Drogi i dostęp.
12. Media i kolizje.
13. Topografia i odwodnienie.
14. Powódź i wody.
15. Geologia i grunty.
16. Środowisko.
17. Zabytki i konserwator.
18. Sąsiedztwo.
19. Procedury.
20. Ryzyka.
21. Unknowns.
22. Next actions.
23. Evidence pack.
24. Zastrzeżenia i ograniczenia analizy.

Formaty:

- Markdown — format bazowy dla Claude Code.
- HTML — format przeglądarkowy.
- PDF — format do udostępnienia.
- JSON — integracje.
- GeoPackage — warstwy GIS.
- DXF — CAD.
- PNG/SVG — mapy statyczne.

---

## 18. Testy i kryteria akceptacji

### 18.1. Testy obowiązkowe

- Unit testy geometrii.
- Unit testy CRS.
- Unit testy rulesetów.
- Unit testy scoringu.
- Testy kontraktowe connectorów.
- Testy integracyjne PostGIS.
- Testy golden parcels.
- Testy parserów MPZP/POG/WZ na ręcznie zweryfikowanym zbiorze.
- Testy snapshotów raportów.
- Testy wydajności quick screening.
- Testy batch dla portfeli.
- Testy MCP protocol.
- Testy bezpieczeństwa uploadów.
- Testy prompt injection z dokumentów.
- Testy partial results przy awarii źródła.
- Testy audytu evidence.

### 18.2. Kryteria akceptacji MVP

MVP jest gotowe, gdy:

- użytkownik może podać identyfikator działki albo punkt;
- system pobiera geometrię i kontekst administracyjny;
- system oblicza geometrię działki;
- system pobiera i raportuje podstawowy status planistyczny;
- system sprawdza najważniejsze warstwy ryzyk: powódź, ochrona przyrody, osuwiska, zabytki, media, drogi;
- system generuje prosty buildable envelope;
- system zwraca red flags, unknowns, next actions i evidence;
- działa MCP `parcel_analyze`, `analysis_get_result`, `report_generate`;
- raport Markdown i JSON są stabilne;
- każda teza ma źródło albo oznaczenie braku źródła;
- awaria jednego connectora daje partial result, a nie pełną porażkę.

### 18.3. Kryteria akceptacji wersji pełnej

Wersja pełna jest gotowa, gdy:

- działa pełna analiza MPZP/POG/WZ z parserami i evidence;
- działa zaawansowany buildable envelope;
- działa scoring inwestycyjny;
- działa batch portfolio;
- działa monitoring zmian;
- działa eksport GIS/CAD;
- rulesety są wersjonowane, testowane i możliwe do aktualizacji bez zmiany kodu;
- analiza jest powtarzalna i audytowalna;
- źródła są cache'owane, healthcheckowane i wersjonowane;
- system nadaje się do użycia przez Claude Code w realnym workflow projektowym.

---

## 19. Roadmapa implementacyjna

### Etap 0 — fundament techniczny

- repo, CI, Docker, PostGIS, modele domenowe;
- MCP skeleton;
- HTTP API skeleton;
- storage, cache, logging;
- basic geometry module.

### Etap 1 — quick screening

- ULDK/parcel resolve;
- geometry metrics;
- core connectors: APP/GML, Geoportal, ISOK, GDOŚ, PIG, NID, GESUT/KIUT;
- simple overlays;
- red flags;
- Markdown/JSON report;
- MCP `parcel_analyze`.

### Etap 2 — planning intelligence

- parsery MPZP/POG/WZ;
- model ustaleń planistycznych;
- rulesets;
- conflicts and confidence;
- evidence trace.

### Etap 3 — buildable envelope i capacity

- setbacks;
- no-build zones;
- largest rectangle;
- capacity scenarios;
- parking and PBC;
- maps and DXF/GPKG export.

### Etap 4 — autonomy and batch

- task graph;
- fallback planner;
- partial results;
- portfolio batch;
- monitoring changes;
- cache warming.

### Etap 5 — hardening

- performance tuning;
- security review;
- parser evaluation;
- legal/ruleset review by experts;
- documentation and deployment.

---

## 20. Instrukcje dla agenta kodującego

1. Zacznij od modeli domenowych i schematów wyników, nie od UI.
2. Zaimplementuj MCP skeleton wcześnie, aby Claude Code mógł sterować analizą w trakcie budowy systemu.
3. Każdy connector implementuj za adapterem z identycznym interfejsem: `fetch`, `normalize`, `snapshot`, `healthcheck`.
4. Każda analiza ma zwracać `result`, `evidence`, `warnings`, `unknowns`, `confidence`.
5. Każdy parser dokumentów ma mieć evaluation set i tryb ręcznej walidacji.
6. Najpierw implementuj szybkie i wiarygodne źródła, potem wolniejsze i mniej pewne fallbacki.
7. Nie projektuj jednej wielkiej funkcji `analyzeEverything()`. Użyj task graph i małych modułów wewnętrznych.
8. Publiczne narzędzia MCP mają być stabilne i niewielkie liczbowo; szczegółowe dane wystawiaj jako resources.
9. Wydajność projektuj od początku: PostGIS, cache, batch, materialized overlays, bbox minimization.
10. Nigdy nie usuwaj niepewności z raportu tylko dlatego, że użytkownik oczekuje prostej odpowiedzi.
11. Nie używaj LLM jako jedynego interpretera prawa miejscowego. LLM może ekstraktować kandydatów, ale reguły i evidence muszą walidować wynik.
12. Implementuj `manual_review_required` jako normalny status, nie jako błąd.
13. Wszystkie dane zewnętrzne zapisuj jako snapshoty albo przynajmniej metadane pobrania.
14. Zadbaj o możliwość pracy offline na zapisanych snapshotach.
15. Dla każdej istotnej decyzji systemu pokaż „dlaczego”.

---

## 21. Antywzorce

- Ukrywanie braku danych.
- Twierdzenie, że działka jest budowlana tylko dlatego, że wygląda na budowlaną na mapie.
- Traktowanie WMS jako danych analitycznych bez świadomości ograniczeń.
- Brak snapshotów źródeł.
- Hardkodowanie przepisów w kodzie.
- Brak dat obowiązywania reguł.
- Brak evidence dla parametrów planu.
- Ładowanie setek drobnych narzędzi MCP do kontekstu.
- Zwracanie wielkich GeoJSON-ów bez paginacji i resources.
- Blokowanie pełnej analizy przez jedną niedostępną usługę.
- Używanie danych pomocniczych jako wiążących.
- Brak manual review gates.
- Brak rozróżnienia między `unknown` i `not_detected`.
- Generowanie porad prawnych bez zastrzeżeń i źródeł.

---

## 22. Minimalny szablon odpowiedzi systemu dla działki

```markdown
# Analiza działki: {parcel_id}

## Decyzja screeningowa
{OK | OK_WITH_RISKS | NEEDS_MANUAL_REVIEW | LIKELY_BLOCKED}

## Najważniejsze wnioski
- ...

## Czerwone flagi
- ...

## Co można rozważać projektowo
- ...

## Co wymaga potwierdzenia
- ...

## Parametry działki
- powierzchnia: ...
- front: ...
- kształt: ...

## Planowanie
- MPZP/POG/WZ: ...
- przeznaczenie: ...
- wskaźniki: ...

## Buildable envelope
- powierzchnia potencjalna: ...
- główne ograniczenia: ...

## Media i dojazd
- ...

## Środowisko, wody, geologia, zabytki
- ...

## Chłonność
- wariant konserwatywny: ...
- wariant bazowy: ...
- wariant optymistyczny: ...

## Następne kroki
- ...

## Źródła i confidence
- ...
```

---

## 23. Uwaga końcowa

Najważniejsza cecha tego narzędzia to nie sama liczba warstw GIS. Kluczowa jest zdolność do autonomicznego zebrania danych, rozpoznania ich jakości, zastosowania reguł, wyjaśnienia wyniku i wskazania, czego nie da się rozstrzygnąć bez człowieka lub dokumentu źródłowego.

---

## 24. NFR — wymagania niefunkcjonalne z identyfikatorami

### 24.1. Wydajność

NFR-PERF-001. `quick_screening` na danych częściowo zcache'owanych powinien zwracać wynik syntetyczny bez oczekiwania na wszystkie wolne źródła.  
NFR-PERF-002. `full_due_diligence` musi być asynchroniczne, wznawialne i możliwe do odczytu jako partial result.  
NFR-PERF-003. Każdy connector ma osobny timeout i nie może blokować całej analizy.  
NFR-PERF-004. Zapytania przestrzenne muszą używać indeksów PostGIS.  
NFR-PERF-005. Operacje overlay dla portfeli muszą być batchowane.  
NFR-PERF-006. Dane powtarzalne dla gminy/powiatu muszą być cache'owane na poziomie jednostki administracyjnej.  
NFR-PERF-007. Warstwy rasterowe wolno próbkować tylko w ograniczonym bbox, nigdy dla całych powiatów bez potrzeby.  
NFR-PERF-008. Raporty duże mają być dzielone na zasoby i sekcje.  
NFR-PERF-009. MCP nie może zwracać ogromnych payloadów bez paginacji, URI zasobu albo filtra.  
NFR-PERF-010. Każdy etap analizy ma emitować status, aby agent mógł podjąć dalsze kroki bez czekania na pełny wynik.  
NFR-PERF-011. System ma wspierać cache warming dla gmin, w których analizowany jest portfel działek.  
NFR-PERF-012. Geometrie do podglądu muszą mieć uproszczoną wersję, a geometrie analityczne zachowują pełną dokładność.  
NFR-PERF-013. Operacje kosztowne, np. cieniowanie 3D, mają być opcjonalne i uruchamiane po wykryciu potrzeby.  
NFR-PERF-014. W workerach stosować backpressure, aby nie zabić zewnętrznych usług publicznych ani własnej bazy.  
NFR-PERF-015. Wydajność ma być mierzona benchmarkami na golden parcels i portfelach testowych.

### 24.2. Niezawodność

NFR-REL-001. Brak odpowiedzi źródła skutkuje statusem `source_unavailable`, nie fałszywym brakiem ograniczeń.  
NFR-REL-002. Każda analiza ma być idempotentna.  
NFR-REL-003. Każdy wynik ma `input_hash`, `source_snapshot_id`, `ruleset_version`.  
NFR-REL-004. System musi umieć odtworzyć raport z historycznego snapshotu.  
NFR-REL-005. Każdy connector ma healthcheck i diagnostykę.  
NFR-REL-006. Dla usług niestabilnych stosować retry z backoff i circuit breaker.  
NFR-REL-007. Dla źródeł konfliktowych raportować konflikt, nie wybierać losowo.  
NFR-REL-008. W trybie batch pojedyncza błędna działka nie może zatrzymać całego portfela.  
NFR-REL-009. Każdy błąd analizy musi być klasyfikowany: input, source, ruleset, geometry, parser, system.  
NFR-REL-010. Wynik częściowy ma być użyteczny i jasno oznaczony.

### 24.3. Audytowalność

NFR-AUD-001. Każdy wniosek musi mieć evidence albo oznaczenie `no_source`.  
NFR-AUD-002. Każdy wskaźnik z planu musi wskazywać dokument, fragment, strefę i metodę ekstrakcji.  
NFR-AUD-003. Każdy override eksperta musi mieć autora, datę, powód i zakres.  
NFR-AUD-004. Raport musi zawierać listę źródeł z datą pobrania.  
NFR-AUD-005. Parsery LLM muszą przechowywać oryginalny fragment, wynik ekstrakcji i walidację schematu.  
NFR-AUD-006. Score musi mieć wyjaśnienie składników.  
NFR-AUD-007. Manual review musi być statusowany i nie może znikać bez akcji użytkownika.  
NFR-AUD-008. Eksport musi zawierać metadane wersji systemu.  
NFR-AUD-009. Dla map należy zapisać CRS, warstwy i parametry stylu.  
NFR-AUD-010. Evidence pack ma być możliwy do pobrania niezależnie od raportu.

### 24.4. Bezpieczeństwo

NFR-SEC-001. Uploady są niezaufane.  
NFR-SEC-002. HTML/PDF/BIP są niezaufane i mogą zawierać prompt injection.  
NFR-SEC-003. Żaden tekst z dokumentu nie może instruktażowo sterować agentem.  
NFR-SEC-004. Connector HTTP musi mieć allowlistę domen albo jawnie zatwierdzony profil źródeł.  
NFR-SEC-005. Nie wykonywać lokalnych komend na podstawie treści dokumentu.  
NFR-SEC-006. Nie zapisywać sekretów w logach, raportach ani artifacts.  
NFR-SEC-007. Pliki użytkownika przetwarzać w sandboxie.  
NFR-SEC-008. Oddzielić dane tenantów.  
NFR-SEC-009. Wprowadzić limity rozmiaru, czasu i liczby stron dokumentów.  
NFR-SEC-010. Każdy tool MCP mający side effects musi być jawnie oznaczony i audytowany.

### 24.5. Jakość domenowa

NFR-DOM-001. System musi używać języka architektonicznego i urbanistycznego zrozumiałego dla profesjonalisty.  
NFR-DOM-002. System musi mieć tryb raportu prostego dla inwestora.  
NFR-DOM-003. System musi zachować rozróżnienie między prawem miejscowym a danymi poglądowymi.  
NFR-DOM-004. System musi znać ograniczenia własnej analizy.  
NFR-DOM-005. System musi obsługiwać niepewność i sprzeczności.  
NFR-DOM-006. Każdy wynik „można budować” musi mieć zakres: co, gdzie, na jakich założeniach i co potwierdzić.  
NFR-DOM-007. Każdy wynik „nie można” musi wskazać, czy to blocker twardy, ryzyko czy brak danych.  
NFR-DOM-008. System musi tworzyć pytania do właściwych specjalistów, nie udawać ich opinii.  
NFR-DOM-009. System musi być gotowy na lokalne definicje pojęć w MPZP.  
NFR-DOM-010. System musi wersjonować interpretacje.

---

## 25. Model confidence i rozstrzyganie konfliktów

### 25.1. Składniki confidence

`confidence_score` powinien być obliczany z co najmniej następujących składników:

```yaml
confidence_components:
  source_authority: 0.00-1.00       # urzędowe vs pomocnicze
  source_freshness: 0.00-1.00       # aktualność
  geometry_precision: 0.00-1.00     # dokładność geometrii
  semantic_precision: 0.00-1.00     # jednoznaczność treści
  parser_confidence: 0.00-1.00      # jakość ekstrakcji, jeśli parser użyty
  cross_source_agreement: 0.00-1.00 # zgodność źródeł
  ruleset_certainty: 0.00-1.00      # jednoznaczność reguły
  manual_verification: 0.00-1.00    # potwierdzenie człowieka
```

Przykładowa polityka:

- `>= 0.85` — wysokie zaufanie, ale nadal z evidence.
- `0.60-0.84` — umiarkowane zaufanie, pokazać zastrzeżenia.
- `0.35-0.59` — niskie zaufanie, wymaga potwierdzenia.
- `< 0.35` — traktować jako hint, nie jako podstawę decyzji.

### 25.2. Konflikty źródeł

Gdy źródła są sprzeczne:

1. Nie usuwać konfliktu z raportu.
2. Wskazać obie wartości i źródła.
3. Zastosować hierarchię wiarygodności.
4. Oznaczyć wynik `manual_review_required`, jeśli konflikt wpływa na decyzję.
5. Zaproponować dokument albo organ, który może konflikt rozstrzygnąć.

### 25.3. Przykłady konfliktów

- GML APP wskazuje inną strefę niż rysunek PDF planu.
- Lokalny SIP ma inne granice MPZP niż Przeglądarka Danych Planistycznych.
- Ortofotomapa pokazuje budynek nieobecny w BDOT/EGiB.
- KIUT/GESUT nie pokazuje sieci, ale mapa do celów projektowych ją pokazuje.
- Warstwa powodziowa ma przecięcie minimalne, ale NMT pokazuje obniżenie terenu.
- MPZP dopuszcza zabudowę, ale działka nie ma dostępu do drogi publicznej.

---

## 26. Minimalny schemat bazy danych

### 26.1. Tabele rdzeniowe

```sql
analysis_runs(id, input_hash, status, mode, ruleset_version, created_at, updated_at, tenant_id)
parcels(id, external_id, teryt, obr, number, geom, area_m2, source_id, snapshot_id)
investment_areas(id, analysis_id, geom, parcel_ids, created_at)
source_records(id, source_type, publisher, url, retrieved_at, license, legal_status, confidence, metadata_json)
evidence_items(id, analysis_id, source_id, subject_type, subject_id, claim, value_json, confidence, geometry, created_at)
planning_acts(id, municipality_id, act_type, title, status, valid_from, valid_to, source_id, metadata_json)
planning_zones(id, act_id, symbol, geom, attributes_json)
constraints(id, analysis_id, constraint_type, severity, confidence, geom, summary, rule_id, source_id)
buildable_envelopes(id, analysis_id, geom, area_m2, confidence, metadata_json)
capacity_scenarios(id, analysis_id, scenario_type, metrics_json, risks_json, geom)
risk_items(id, analysis_id, risk_type, severity, confidence, status, summary, mitigation, source_id)
unknown_items(id, analysis_id, topic, severity, reason, suggested_action)
report_artifacts(id, analysis_id, artifact_type, uri, content_hash, created_at)
overrides(id, analysis_id, user_id, target_type, target_id, before_json, after_json, reason, created_at)
```

### 26.2. Indeksy wymagane

```sql
CREATE INDEX parcels_geom_gix ON parcels USING GIST (geom);
CREATE INDEX planning_zones_geom_gix ON planning_zones USING GIST (geom);
CREATE INDEX constraints_geom_gix ON constraints USING GIST (geom);
CREATE INDEX buildable_envelopes_geom_gix ON buildable_envelopes USING GIST (geom);
CREATE INDEX evidence_items_geometry_gix ON evidence_items USING GIST (geometry);
CREATE INDEX source_records_retrieved_idx ON source_records (retrieved_at);
CREATE INDEX analysis_runs_input_hash_idx ON analysis_runs (input_hash);
CREATE INDEX planning_acts_municipality_idx ON planning_acts (municipality_id, act_type, status);
```

### 26.3. Zasady przechowywania geometrii

- Geometria analityczna w EPSG:2180 dla Polski.
- Geometria wejściowa przechowywana w oryginalnym CRS jako metadane.
- Geometria uproszczona jako oddzielna kolumna albo materialized view.
- Raster data nie trzymać w bazie, jeśli wystarczy object storage + indeksy kafli.
- Duże GeoJSON-y udostępniać jako pliki/zasoby, nie inline w odpowiedzi MCP.

---

## 27. HTTP API orientacyjne

```text
POST   /v1/parcels/resolve
POST   /v1/analyses
GET    /v1/analyses/{analysis_id}
GET    /v1/analyses/{analysis_id}/status
GET    /v1/analyses/{analysis_id}/result
GET    /v1/analyses/{analysis_id}/evidence
GET    /v1/analyses/{analysis_id}/risks
GET    /v1/analyses/{analysis_id}/unknowns
GET    /v1/analyses/{analysis_id}/buildable-envelope
POST   /v1/analyses/{analysis_id}/reports
GET    /v1/artifacts/{artifact_id}
POST   /v1/documents/ingest
POST   /v1/portfolio/analyze
POST   /v1/monitoring
GET    /v1/sources/health
POST   /v1/cache/warm
GET    /v1/rulesets
GET    /v1/rulesets/{version}
POST   /v1/overrides
```

API i MCP powinny korzystać z tych samych use-case'ów domenowych, aby uniknąć rozjazdu logiki.

---

## 28. Definition of Done dla connectora

Connector jest gotowy, gdy:

- ma opis źródła, regulaminu, licencji i ograniczeń;
- ma test healthcheck;
- obsługuje timeout, retry i circuit breaker;
- normalizuje wynik do modelu kanonicznego;
- zapisuje `SourceRecord` i `EvidenceItem`;
- odróżnia brak danych od braku odpowiedzi;
- ma mock/fixture do CI;
- ma test kontraktowy na przykładowych danych;
- ma metryki czasu odpowiedzi i błędów;
- ma dokumentację fallbacku.

---

## 29. Definition of Done dla parsera dokumentu planistycznego

Parser jest gotowy, gdy:

- przyjmuje PDF, HTML, DOCX albo tekst, ale jasno oznacza typ źródła;
- ekstrahuje kandydatów do ustaleń w strukturze JSON;
- waliduje wynik JSON Schema;
- wskazuje fragment źródłowy dla każdego ustalenia;
- odróżnia ustalenia ogólne od szczegółowych;
- obsługuje symbole terenów;
- obsługuje lokalne definicje pojęć;
- wykrywa brak jednoznacznego powiązania symbolu z działką;
- ma zestaw golden documents;
- ma metryki precision/recall dla wybranych parametrów;
- nigdy nie traktuje wyniku LLM jako wiążącego bez confidence i evidence;
- umie zwrócić `manual_review_required`.

---

## 30. Definition of Done dla buildable envelope

Buildable envelope jest gotowy, gdy:

- działa na geometrii jednej i wielu działek;
- uwzględnia granice, linie zabudowy i strefy wyłączone;
- obsługuje różne źródła linii zabudowy z różnym confidence;
- rozróżnia ograniczenia twarde i miękkie;
- pokazuje, które ograniczenie odjęło jaką powierzchnię;
- generuje wynik geometryczny i opisowy;
- generuje największe prostokąty/warianty footprintu;
- zapisuje trace operacji przestrzennych;
- ma testy na działkach prostych, wąskich, narożnych, nieregularnych i wieloczęściowych;
- ma eksport GeoJSON/GPKG/DXF.

---

## 31. Definition of Done dla raportu

Raport jest gotowy, gdy:

- ma executive summary;
- zawiera parametry działki;
- pokazuje decyzję screeningową;
- pokazuje planowanie, ograniczenia i ryzyka;
- zawiera mapę buildable envelope;
- zawiera unknowns i next actions;
- zawiera evidence pack albo link do niego;
- ma jawne zastrzeżenia;
- działa w Markdown i JSON;
- PDF/HTML są renderowane z tego samego modelu raportu;
- dane liczbowe są spójne z JSON-em;
- raport można odtworzyć ze snapshotu.

---

## 32. Priorytety MVP — kolejność implementacji funkcji

### Must have

- parcel resolve;
- geometria działki;
- source/evidence model;
- podstawowe connectory: ULDK, APP/GML/BIP, ISOK, GDOŚ, PIG SOPO, NID, GESUT/KIUT, NMT;
- overlay engine;
- red flags;
- buildable envelope v1;
- raport Markdown/JSON;
- MCP `parcel_analyze`, `analysis_get_result`, `report_generate`;
- cache i partial results;
- status `manual_review_required`.

### Should have

- parser MPZP/POG/WZ;
- scoring;
- warianty capacity;
- DXF/GPKG export;
- portfolio batch;
- monitoring zmian;
- advanced confidence model;
- UI map preview.

### Could have

- 3D shadows;
- IFC massing;
- rynek nieruchomości;
- kosztorys wysokiego poziomu;
- automatyczna korespondencja;
- integracja z BIM/CDE;
- modele ML do detekcji drzew i obiektów.

### Won't have w MVP

- wiążąca opinia prawna;
- automatyczne złożenie wniosku administracyjnego;
- pełna dokumentacja projektowa;
- gwarancja zgodności bez przeglądu uprawnionego projektanta;
- zastąpienie mapy do celów projektowych;
- zastąpienie badań geotechnicznych.
