import json, os, re, sys
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
BASE='https://api.kleinanzeigen-agent.de/api/v2/kleinanzeigen'; KEY=os.environ.get('KLAZ_API_KEY','').strip()
STATE='data/state.json'; OUT='data/results.json'
def read(path, default):
 try:
  with open(path,encoding='utf-8') as f:return json.load(f)
 except (OSError,json.JSONDecodeError):return default
def write(path,obj):
 os.makedirs(os.path.dirname(path),exist_ok=True)
 with open(path,'w',encoding='utf-8') as f:json.dump(obj,f,ensure_ascii=False,indent=2)
def api(path,params=None):
 if not KEY:raise RuntimeError('Secret KLAZ_API_KEY fehlt. Siehe README.md.')
 url=BASE+path+(('?'+urlencode(params)) if params else '')
 req=Request(url,headers={'klaz_key':KEY,'Accept':'application/json','User-Agent':'AutoDealScanner/0.1'})
 try:
  with urlopen(req,timeout=45) as r:data=json.loads(r.read().decode())
 except HTTPError as e:raise RuntimeError(f'API-Fehler HTTP {e.code}: {e.read().decode("utf-8","replace")[:400]}')
 except URLError as e:raise RuntimeError(f'API-Verbindung fehlgeschlagen: {e}')
 if data.get('success') is False:raise RuntimeError(data.get('message','API-Suche fehlgeschlagen'))
 return data
def walk(o):
 if isinstance(o,dict):
  yield o
  for v in o.values():yield from walk(v)
 elif isinstance(o,list):
  for v in o:yield from walk(v)


def location_id(payload):
    candidates = []

    for item in walk(payload):
        if not isinstance(item, dict):
            continue

        name = str(item.get("name", "")).strip().lower()
        city = str(item.get("city", "")).strip().lower()
        label = str(item.get("label", "")).strip().lower()

        identifier = (
            item.get("location_id")
            or item.get("id")
        )

        matches = (
            name == "kassel"
            or city == "kassel"
            or label == "kassel"
            or name.startswith("kassel,")
            or city.startswith("kassel,")
            or label.startswith("kassel,")
        )

        if identifier is not None and matches:
            candidates.append(
                (0 if name == "kassel" or city == "kassel" else 1,
                 str(identifier))
            )

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def category_id(payload):
 found=[]
 for x in walk(payload):
  name=str(x.get('name','')).lower().strip(); ident=x.get('category_id',x.get('id'))
  if ident is not None and name in ('autos','pkw'):found.append((0 if name=='autos' else 1,str(ident)))
 return sorted(found)[0][1] if found else None
def price_of(a):
 p=a.get('price');p=p.get('amount') if isinstance(p,dict) else p
 try:return float(p)
 except (ValueError,TypeError):return None
def carish(a):
 t=(str(a.get('title',''))+' '+str(a.get('description',''))).lower()
 exclude=('autoanhänger','anhänger','felge','reifen','motoröl','ersatzteil','scheinwerfer','getriebeöl','suche auto')
 brands=('volkswagen','vw ','golf','polo','passat','opel','astra','corsa','ford','focus','fiesta','bmw','mercedes','benz','audi','skoda','seat','renault','peugeot','citroën','citroen','dacia','volvo','fiat','toyota','hyundai','kia','mazda','honda','nissan','suzuki','gebrauchtwagen','pkw','auto ')
 return not any(x in t for x in exclude) and any(x in t for x in brands)
def reserve(a,p):
 t=(str(a.get('title',''))+' '+str(a.get('description',''))).lower()
 if any(x in t for x in ('motorschaden','getriebeschaden','unfallwagen','bastlerfahrzeug','nicht fahrbereit','springt nicht an','kupplung defekt')):return max(1500,p*.35)
 if any(x in t for x in ('tüv abgelaufen','tuev abgelaufen','rost','klima defekt','kratzer','delle','reparatur','defekt','ölverlust')):return max(900,p*.18)
 return max(600,p*.10)
def key_for(title):
 words=re.sub(r'[^a-z0-9äöüß ]',' ',title.lower()).split();makes=('volkswagen','vw','opel','ford','bmw','mercedes','benz','audi','skoda','seat','renault','peugeot','citroen','citroën','dacia','volvo','fiat','toyota','hyundai','kia','mazda','honda','nissan','suzuki')
 for i,w in enumerate(words):
  if w in makes:return (w+' '+words[i+1] if i+1<len(words) else w)
 return ' '.join(words[:2])
def main():
 if not KEY:raise RuntimeError('KLAZ_API_KEY ist nicht gesetzt. GitHub → Settings → Secrets and variables → Actions → New repository secret.')
 state=read(STATE,{ });loc=state.get('location_id');cat=state.get('category_id');discovery=0
 if not loc:
  
location_response = api("/locations", {"q": "Kassel", "limit": 100})
print("DEBUG Standort-Antwort:")
print(json.dumps(location_response, ensure_ascii=False, indent=2)[:10000])

loc = location_id(location_response)

if not loc:
    raise RuntimeError(
        "Kassel wurde in der Standort-Antwort nicht gefunden. "
        "Bitte die DEBUG-Ausgabe prüfen."
    )
     
 if not cat:
  cat=category_id(api('/categories'));discovery+=1
  if not cat:raise RuntimeError('Auto-Kategorie-ID nicht automatisch erkannt. Siehe README.md.')
 state.update(location_id=str(loc),category_id=str(cat));unique={};errors=[]
 for q in ('Auto','Gebrauchtwagen','PKW'):
  try:
   d=api('/search',{'q':q,'location_id':str(loc),'distance':'100','min_price':'100','max_price':'7000','category_id':str(cat),'ad_type':'OFFER','page':'0','size':'100'})
   for a in d.get('data',{}).get('ads',[]) or []:
    aid=str(a.get('ad_id') or a.get('ad_url') or '')
    if aid:unique[aid]=a
  except Exception as e:errors.append(q+': '+str(e))
 if not unique and errors:raise RuntimeError('Alle Suchen fehlgeschlagen: '+'; '.join(errors))
 ads=[a for a in unique.values() if price_of(a) is not None and 100<=price_of(a)<=7000 and carish(a)]
 groups={}
 for a in ads:groups.setdefault(key_for(str(a.get('title',''))),[]).append(price_of(a))
 scored=[]
 for a in ads:
  p=price_of(a);k=key_for(str(a.get('title',''))); comps=[x for x in groups.get(k,[]) if p*.65<=x<=p*2.5 and abs(x-p)>100]
  if len(comps)>=2:
   comps.sort();market=comps[len(comps)//2];note=f'Grobe Vergleichsgruppe ({len(comps)} Treffer); Modell/Baujahr nicht genau abgeglichen';profit=round(market-p-reserve(a,p)-max(250,p*.08))
  else:market=p;note='Zu wenige belastbare Vergleichsangebote; Gewinn nicht verlässlich schätzbar';profit=-9999
  lo=a.get('location') or {};scored.append({'id':str(a.get('ad_id') or ''),'title':str(a.get('title') or 'Fahrzeugangebot'),'price':round(p),'estimated_market_value':round(market),'cost_reserve':round(reserve(a,p)+max(250,p*.08)),'estimated_profit':profit,'estimation_note':note,'location':lo.get('name') or lo.get('city') or 'Kassel-Umkreis','created_at':a.get('created_at'),'url':a.get('ad_url') or ''})
 scored.sort(key=lambda x:(x['estimated_profit']<800,-x['estimated_profit'],x['price']))
 write(OUT,{'updated_at':datetime.now(timezone.utc).isoformat(),'searches_run':3+discovery,'ads':scored,'note':'Schätzwerte sind vorläufig. Inserate ohne ausreichende Vergleichsdaten werden nicht als profitabel ausgewiesen.'});write(STATE,state)
 print(f'Fertig: {len(scored)} Fahrzeuge ausgewertet. Suchabfragen: 3; Ermittlung der IDs: {discovery}.')
 if errors:print('Einzelne Fehler: '+'; '.join(errors))
if __name__=='__main__':
 try:main()
 except Exception as e:print('FEHLER:',e,file=sys.stderr);sys.exit(1)
