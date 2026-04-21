# SolarLead — Test end-to-end a basso costo

> Obiettivo: eseguire il **15-step smoke test** (vedi `SMOKE_TEST.md`) verificando ogni flusso senza bruciare budget.
> Budget minimo stimato per un **full-flow run completo**: **€15-30**.
> Se vuoi limitarti a "ci gira tutto senza spendere un euro", possiamo testare ~70% dei flussi (no WhatsApp reali, no cartoline reali).

---

## 1. Quanto costa un run smoke test completo?

Ipotesi: **1 tenant test**, **1 territorio**, **10 lead reali**, **3 email outreach**, **1 WhatsApp**, **1 cartolina**.

| Servizio | Uso nel smoke | Costo stimato |
|---|---|---|
| Anthropic Claude (scoring + creative + reply) | ~50k token | **~€0,40** |
| Resend email (3 send + 3 delivered + 3 opened) | 3 email | **€0** (free tier 3k/mese) |
| NeverBounce (10 verifiche) | 10 | **~€0,07** |
| 360dialog (1 messaggio inbound + 1 outbound) | 2 msg | **€0** con sandbox, **€39** se apri piano |
| Pixart (1 cartolina reale) | 1 | **~€1,50** |
| Mapbox geocoding + map load | <100 calls | **€0** (free tier) |
| Hunter.io (se attivo, 10 enrich) | 10 | **€0** (25 gratis poi piano) |
| Supabase / Railway / Vercel | bassissimo | **€0** (already paid base) |
| **TOTALE PER RUN COMPLETO** | | **~€2 + 360dialog se ti serve reale** |

**Senza WhatsApp reale e senza cartolina reale** (mockando gli step 11 e 12 dello smoke): **< €0,50 per run.**

Puoi quindi fare **5-10 run** del protocollo completo in budget totale **< €15**.

---

## 2. Crediti API: cosa procurarsi prima di iniziare

### 2.1 Anthropic (OBBLIGATORIO)

- Account: https://console.anthropic.com/
- **Crediti di benvenuto**: $5 ($5 free al primo signup, a volte varia)
- **Top-up consigliato per test**: **$10-15** (copre >300k token di Sonnet 4.5, ovvero decine di run smoke)
- Dove inserire la key: `ANTHROPIC_API_KEY` nell'env Railway (service `api` + service `worker`).
- **Hard limit consigliato**: imposta un **monthly spend limit a $20** in Console → Settings → Limits, così eviti sorprese se un bug rigira Claude in loop.

### 2.2 Resend (OBBLIGATORIO)

- Account: https://resend.com/
- **Free tier**: **3.000 email/mese, 100/giorno, 1 dominio**. È ampissimo per il test.
- Cosa serve fare:
  1. Verificare un dominio di test (es. `test.solarlead.it` o un sottodominio di un dominio che possiedi già)
  2. Configurare DKIM + SPF + DMARC record (li genera Resend)
  3. Copiare l'API key in `RESEND_API_KEY`
  4. Configurare webhook URL `https://<api-url>/v1/webhooks/resend` e copiare signing secret in `RESEND_WEBHOOK_SECRET`
- **Inbound email**: richiede anche record MX. Per test puoi usare un sottodominio tipo `mail.test.solarlead.it` e puntare MX a Resend.
- **Non serve nessun top-up**: se resti sotto 3k/mese è gratis.

### 2.3 NeverBounce (OPZIONALE ma consigliato)

- Account: https://neverbounce.com/
- **Crediti gratuiti al signup**: **1.000 verifiche gratis** (campagna sign-up, verifica attuale al momento della registrazione).
- Per test: **basta il free tier, zero spesa**.
- Dove inserire: `NEVERBOUNCE_API_KEY` (oppure salvato per tenant in `tenants.settings.neverbounce_api_key`).

### 2.4 360dialog + WhatsApp Business (IL PIÙ INSIDIOSO)

Questo è il servizio dove **non c'è un vero free tier**. Due strade:

**Strada A — Test SENZA WhatsApp reale (€0):**
- Nel flow marca lo step 11 dello smoke test come "skipped".
- Nel codice `ConversationAgent` puoi forzare un mock via env var tipo `DIALOG360_MOCK=true` che bypassa la chiamata e logga il payload che avresti inviato.
- Verifichi tutto il resto (scan, email, reply, tracking, portal, postal) senza spendere nulla su WhatsApp.

**Strada B — Test CON WhatsApp reale (~€39-50):**
- Crea account 360dialog hub: https://hub.360dialog.com/
- Attiva **un numero di prova Meta WhatsApp Business**. 360dialog ha un piano **"Partner Sandbox"** (o simile) che permette di inviare/ricevere messaggi a numeri **pre-registrati** (il tuo telefono personale e max 4 numeri amici) **gratuitamente per 30 giorni**.
- Se non trovi il sandbox: piano minimo ~€39/mese. Puoi cancellarlo dopo 1 mese.
- Configura webhook `https://<api-url>/v1/webhooks/whatsapp?tenant_id=<uuid>` con signing secret in `DIALOG360_WEBHOOK_SECRET`.

**Raccomandazione**: **fai Strada A** per i primi 5-10 run smoke. Passa a **Strada B** solo quando hai tutto il resto verde e vuoi la demo "vera" per il cliente pilota.

### 2.5 Pixart (cartoline postali)

- Sito: https://www.pixartprinting.it/ — hanno API/portale B2B
- Non c'è free tier (è stampa fisica, costa carta+inchiostro+postage).
- **Costo minimo test**: 1 cartolina A5 = **~€1,50** (stampa + spedizione Italia)
- Webhook config: `https://<api-url>/v1/webhooks/pixart` con `PIXART_WEBHOOK_SECRET`
- **Alternativa test a €0**: simula il webhook con `curl` per verificare la logica TrackingAgent senza stampare davvero:
  ```bash
  PAYLOAD='{"tracking_code":"TEST123","event_type":"delivered"}'
  SIG=$(echo -n "$PAYLOAD" | openssl dgst -sha256 -hmac "$PIXART_WEBHOOK_SECRET" -hex | cut -d' ' -f2)
  curl -X POST https://<api>/v1/webhooks/pixart \
    -H "X-Pixart-Signature: $SIG" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD"
  ```
  Questo fa partire l'event `lead.postal_delivered` esattamente come se la cartolina fosse arrivata.

### 2.6 Mapbox

- Account: https://account.mapbox.com/
- **Free tier ENORME**: 50k map loads/mese + 100k geocoding/mese.
- **Non spenderai un euro** finché non hai centinaia di tenant attivi.
- Token da inserire nel dashboard Vercel: `NEXT_PUBLIC_MAPBOX_TOKEN`.

### 2.7 Hunter.io (se usato)

- Se il codice lo usa: free tier 25 search/mese al signup.
- Piano minimo pagato: €49/mese per 500 ricerche.
- Per test iniziale: **non prendere piano, usa le 25 gratis**.

---

## 3. Piatta finanziamento totale per i primi 60 giorni

Assumendo: 10 run smoke + sviluppo attivo + 1 cliente pilota reale a metà del mese 2.

| Voce | Mese 1 | Mese 2 | Totale 60gg |
|---|---|---|---|
| Supabase Pro | €23 | €23 | €46 |
| Railway Pro + uso | €22 | €25 | €47 |
| Vercel Pro (o Pro Trial → Pro) | €18 | €18 | €36 |
| Anthropic Claude (top-up) | €15 | €30 | €45 |
| Resend (free) | €0 | €0 | €0 |
| NeverBounce (free tier) | €0 | €5 | €5 |
| 360dialog (solo da mese 2 con cliente reale) | €0 | €45 | €45 |
| Pixart (test + 1 cliente) | €5 | €30 | €35 |
| Dominio SolarLead + dominio cliente | €15 | €0 | €15 |
| Buffer imprevisti | €20 | €20 | €40 |
| **TOTALE** | **€118** | **€196** | **~€314** |

**Rientro con 1 cliente pagante a €297 + setup €497 al mese 2**: **€794 incassati** → **margine €478** nei primi 60 giorni.

---

## 4. Setup ambiente test — checklist concreta

Esegui **in ordine**, spuntando man mano.

### 4.1 Crea gli account e carica crediti (1 ora)
- [ ] Anthropic: signup + $10 top-up + spending limit $20 + copia `ANTHROPIC_API_KEY`
- [ ] Resend: signup + verifica dominio test + `RESEND_API_KEY` + `RESEND_WEBHOOK_SECRET`
- [ ] NeverBounce: signup free + `NEVERBOUNCE_API_KEY`
- [ ] Mapbox: signup + public token (`NEXT_PUBLIC_MAPBOX_TOKEN`)
- [ ] 360dialog: **skip per ora**, marca `DIALOG360_MOCK=true`
- [ ] Pixart: **skip per ora**, testa con curl firmato

### 4.2 Configura Railway env (15 min)
Nel tab **Variables** del service `api`:

```
ANTHROPIC_API_KEY=sk-ant-...
RESEND_API_KEY=re_...
RESEND_WEBHOOK_SECRET=<openssl rand -hex 32>
NEVERBOUNCE_API_KEY=nb_...
DIALOG360_WEBHOOK_SECRET=<openssl rand -hex 32>
PIXART_WEBHOOK_SECRET=<openssl rand -hex 32>
DIALOG360_MOCK=true
APP_ENV=staging
```

**Stessi valori sul service `worker`** (usano la stessa config).

### 4.3 Configura Vercel env dashboard (5 min)
```
NEXT_PUBLIC_SUPABASE_URL=https://ppabjpryzzkksrbnledy.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=<anon key Supabase>
NEXT_PUBLIC_API_URL=https://api-production-b5c97.up.railway.app
NEXT_PUBLIC_LEAD_PORTAL_URL=https://<portal-url-quando-deploy>
NEXT_PUBLIC_MAPBOX_TOKEN=pk.eyJ...
```

### 4.4 Seed tenant di test (10 min)
Dal wizard `/onboarding` in incognito:
- Nome: "Test Solare Napoli"
- Dominio email: il tuo dominio Resend verificato (es. `test.solarlead.it`)
- Brand color: qualsiasi hex
- Logo: upload immagine qualsiasi
- Territorio: CAP 80100 (Napoli centro)

### 4.5 Esegui primo smoke test (45 min)
- Segui i 15 step di `SMOKE_TEST.md`.
- Gli step 11 (WhatsApp reale) e 12 (cartolina reale) **skip o mocka con curl**.
- Se uno step fallisce: guarda `Railway → Logs` del service coinvolto (api per webhook, worker per task).

### 4.6 Fix iterativo
Se uno step fallisce, ripara e rilancia SOLO lo step fallito, non tutto il protocollo. Aspettarsi **2-3 run completi** prima che tutto passi verde.

---

## 5. Hard limit & guardrail per non bruciare budget

- [ ] **Anthropic**: spending limit $20/mese in console. Se lo tocchi, stop e investiga — probabilmente c'è un loop nel codice.
- [ ] **Resend**: free tier è self-limited a 3k/mese. Se superi ricevi email di avviso.
- [ ] **Railway**: imposta usage alerts a $50 nel billing.
- [ ] **360dialog**: se attivi piano pagante, NON lasciarlo attivo tra un round di test e l'altro. Cancella sub alla fine del mese se non hai ancora clienti paganti.
- [ ] **Pixart**: nessun abbonamento, solo pay-per-send. Non può scapparti di mano.

---

## 6. Cosa NON testare nel primo round

Per mantenere basso lo scope iniziale, rimanda:

- ❌ **Test multi-tenant isolamento RLS** (lo fai dopo il primo cliente pagante, quando serve davvero)
- ❌ **Test load** (a 1 cliente non serve)
- ❌ **A/B experiments completi** (serve volume > 100 lead/variant per dati utili)
- ❌ **CRM webhook outbound reali** (usa webhook.site come sink per test)

Concentrati sui **15 step** di `SMOKE_TEST.md` con volumi minimi.

---

## 7. Rollback plan se qualcosa va storto

Scenario: spendi €50+ in un giorno per bug Claude in loop.

1. **Revoca immediatamente l'API key** dalla console Anthropic
2. Scala a zero i service Railway (o stop deploy)
3. Ispeziona logs Railway per trovare la funzione che ha generato il loop
4. Fix + aggiunta di `max_retries=2` e dedup cache in `arq`
5. Genera nuova API key + spending limit più aggressivo ($10)
6. Riprova 1 step alla volta, non tutto il protocollo insieme

**Lezione preventiva**: prima di ogni smoke test esegui dal PC un `arq` locale con un dry-run invece del worker staging, così catturi loop infiniti a costo zero.

---

## 8. Quando puoi dire "il sistema è testato e pronto per cliente pagante"

**Exit criteria**:
- [ ] 2 run smoke consecutivi completamente verdi (tutti 15 step)
- [ ] Budget Claude sotto $2 per run (se è di più: token troppo alti, ottimizza)
- [ ] Latency media per step sotto target (vedi SLA in SMOKE_TEST.md)
- [ ] Nessun errore 5xx nei logs Railway ultima giornata
- [ ] RLS test: da tenant B non si leggono leads di tenant A (SELECT via service_role vs anon)
- [ ] 1 email reale arrivata a inbox Gmail + Apple Mail con rendering corretto

Quando tutti verdi → fai la call con zio e attiva il primo pilota reale.
