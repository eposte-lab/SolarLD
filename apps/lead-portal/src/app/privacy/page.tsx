import type { Metadata } from 'next';
import Link from 'next/link';

import { fetchPublicLead, type PublicLead } from '@/lib/api';

export const metadata: Metadata = {
  title: 'Informativa Privacy · SolarLead',
  description:
    'Informativa sul trattamento dei dati personali ai sensi del Regolamento UE 2016/679 (GDPR).',
  robots: { index: true, follow: true },
};

/**
 * Privacy policy — fallback per la piattaforma SolarLead.
 *
 * Quando un tenant configura `tenants.privacy_policy_url`, il link nel
 * checkbox GDPR del form sopralluogo punta lì. Questa pagina è la
 * fallback: copre il trattamento "di piattaforma" (lead-portal),
 * dichiarando il modello di responsabilità — il Tenant (installatore)
 * è Titolare del trattamento per i propri prospect; SolarLead è
 * Responsabile (data processor) tramite contratto ex art. 28 GDPR.
 */
export default async function PrivacyPolicyPage({
  searchParams,
}: {
  searchParams: Promise<{ slug?: string }>;
}) {
  const lastUpdated = '14 maggio 2026';
  // Con `?slug=` l'informativa è intestata al tenant (Titolare del
  // trattamento) del lead; senza slug resta la versione generica di
  // piattaforma.
  const { slug } = await searchParams;
  let tenant: PublicLead['tenant'] = null;
  if (slug) {
    const res = await fetchPublicLead(slug);
    if (res.kind === 'ok') tenant = res.lead.tenant;
  }
  const titolare = tenant?.legal_name || tenant?.business_name || null;

  return (
    <main className="min-h-screen bg-surface text-on-surface">
      <header className="bg-surface-container">
        <div className="mx-auto max-w-3xl px-6 py-8">
          <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
            {titolare ? (
              titolare
            ) : (
              <Link href="/" className="hover:underline">
                SolarLead
              </Link>
            )}
            {' · '}Informativa
          </p>
          <h1 className="mt-2 font-headline text-3xl font-bold tracking-tighter md:text-4xl">
            Informativa sul trattamento dei dati personali
          </h1>
          <p className="mt-2 text-sm text-on-surface-variant">
            ai sensi degli artt. 13 e 14 del Regolamento (UE) 2016/679
            («GDPR»)
          </p>
          <p className="mt-1 text-xs text-on-surface-variant">
            Ultimo aggiornamento: {lastUpdated}
          </p>
        </div>
      </header>

      <article className="mx-auto max-w-3xl px-6 py-10 text-[15px] leading-relaxed text-on-surface">
        <Section title="1. Premessa e ambito di applicazione">
          <P>
            La presente Informativa descrive le modalità con le quali
            vengono trattati i dati personali degli utenti che accedono al
            portale lead-facing (la <em>«dossier page»</em> personalizzata
            raggiungibile tramite il link inviato via email)
            e che interagiscono con i moduli ivi presenti, in particolare
            con il modulo di richiesta sopralluogo e con la funzione di
            caricamento bolletta elettrica.
          </P>
          <P>
            Il portale è fornito da <strong>SolarLead</strong>{' '}
            (in seguito anche «Piattaforma»), in qualità di Responsabile
            del trattamento ai sensi dell&apos;art. 28 GDPR, sulla base
            di un contratto di nomina stipulato con l&apos;impresa
            installatrice (il <strong>«Titolare del trattamento»</strong>),
            i cui dati identificativi sono indicati nel footer della
            pagina del lead e nella corrispondenza email ricevuta.
          </P>
        </Section>

        <Section title="2. Titolare del trattamento">
          {titolare ? (
            <>
              <P>
                <strong>Titolare del trattamento</strong> è{' '}
                <strong>{titolare}</strong>
                {tenant?.legal_address ? `, con sede in ${tenant.legal_address}` : ''}
                {tenant?.vat_number ? ` — P.IVA ${tenant.vat_number}` : ''}.
              </P>
              <P>
                Per esercitare i vostri diritti potete contattare
                direttamente il Titolare
                {tenant?.contact_email ? (
                  <>
                    {' '}all&apos;indirizzo{' '}
                    <a
                      href={`mailto:${tenant.contact_email}`}
                      className="underline"
                    >
                      {tenant.contact_email}
                    </a>
                  </>
                ) : (
                  ' ai recapiti indicati nel footer della pagina del dossier'
                )}
                .
              </P>
            </>
          ) : (
            <>
              <P>
                <strong>Titolare del trattamento</strong> è l&apos;azienda
                installatrice che vi ha contattato e i cui dati
                identificativi (ragione sociale, sede legale, P.IVA,
                recapiti) sono indicati nel footer della pagina del dossier
                e nelle comunicazioni che avete ricevuto.
              </P>
              <P>
                Per esercitare i vostri diritti potete contattare
                direttamente il Titolare ai recapiti indicati, oppure
                scrivere all&apos;indirizzo email di supporto della
                Piattaforma:{' '}
                <a
                  href="mailto:privacy@solarlead.it"
                  className="underline"
                >
                  privacy@solarlead.it
                </a>
                .
              </P>
            </>
          )}
        </Section>

        <Section title="3. Responsabile del trattamento (Piattaforma)">
          <P>
            <strong>SolarLead</strong> agisce in qualità di Responsabile
            del trattamento ex art. 28 GDPR ed eroga l&apos;infrastruttura
            tecnica (rendering, calcolo ROI, hosting del portale,
            piattaforma di outreach) per conto del Titolare. La
            Piattaforma non utilizza i dati ricevuti per finalità
            proprie e non li condivide con soggetti terzi al di fuori di
            quanto indicato al paragrafo 7.
          </P>
        </Section>

        <Section title="4. Tipologie di dati trattati">
          <P>I dati personali oggetto del trattamento includono:</P>
          <ul className="ml-5 mt-2 list-disc space-y-1.5">
            <li>
              <strong>Dati identificativi e di contatto</strong>: nome,
              cognome, ragione sociale (se persona giuridica), numero di
              telefono, indirizzo email, P.IVA, indirizzo della sede o
              dell&apos;immobile oggetto della proposta;
            </li>
            <li>
              <strong>Dati tecnici relativi all&apos;immobile</strong>:
              indirizzo, coordinate geografiche, superficie del tetto,
              esposizione, eventuale documentazione catastale fornita
              spontaneamente;
            </li>
            <li>
              <strong>Dati di consumo energetico</strong>: contenuto della
              bolletta elettrica eventualmente caricata (consumo annuo in
              kWh, spesa annua in euro, fornitore, profilo tariffario),
              estratti tramite OCR automatico o inseriti manualmente;
            </li>
            <li>
              <strong>Dati di navigazione</strong>: indirizzo IP, user
              agent, referrer, eventi di interazione con la pagina
              (visualizzazioni, clic, tempo di permanenza), apertura
              della pagina dossier, click sui CTA;
            </li>
            <li>
              <strong>Dati forniti volontariamente</strong>: contenuto
              del campo «Note» del form sopralluogo, orario preferito
              di contatto, risposte alle email.
            </li>
          </ul>
          <P className="mt-3">
            Non vengono trattate categorie particolari di dati ex art. 9
            GDPR (es. dati sanitari, biometrici, orientamento sessuale).
            Vi invitiamo pertanto a non inserire tali informazioni nei
            campi liberi.
          </P>
        </Section>

        <Section title="5. Finalità e basi giuridiche del trattamento">
          <P>I vostri dati sono trattati per le seguenti finalità:</P>
          <ul className="ml-5 mt-2 list-disc space-y-1.5">
            <li>
              <strong>(a) Riscontro alla vostra richiesta di sopralluogo
              o di informazioni</strong>: base giuridica art. 6.1.b GDPR
              (esecuzione di misure precontrattuali su vostra richiesta);
            </li>
            <li>
              <strong>(b) Generazione della proposta personalizzata</strong>{' '}
              (rendering del tetto, stima ROI, calcolo bolletta) sulla
              base dei dati pubblicamente disponibili relativi
              all&apos;immobile e di quelli da voi forniti: base giuridica
              art. 6.1.f GDPR (legittimo interesse del Titolare a
              fornire una proposta commerciale documentata) ed eventualmente
              art. 6.1.a (consenso) per il caricamento della bolletta;
            </li>
            <li>
              <strong>(c) Invio di comunicazioni di marketing diretto</strong>{' '}
              relative all&apos;offerta solare/fotovoltaica del Titolare:
              base giuridica art. 6.1.f GDPR (legittimo interesse) per il
              soft-opt-in di cui all&apos;art. 130 c. 4 D.Lgs. 196/2003,
              con facoltà di opporsi in qualsiasi momento tramite il link
              «Non voglio più ricevere comunicazioni»;
            </li>
            <li>
              <strong>(d) Adempimento di obblighi di legge</strong> in
              materia fiscale, contabile e di lotta agli illeciti: base
              giuridica art. 6.1.c GDPR.
            </li>
          </ul>
        </Section>

        <Section title="6. Modalità del trattamento">
          <P>
            I dati sono trattati con strumenti informatici e telematici,
            con logiche strettamente correlate alle finalità indicate e,
            comunque, in modo da garantire la sicurezza e la riservatezza
            ai sensi degli artt. 5 e 32 GDPR. Sono adottate misure tecniche
            e organizzative adeguate, tra cui: crittografia in transito
            (TLS 1.3), cifratura at-rest dei database, segregazione
            multi-tenant a livello applicativo, autenticazione forte
            sull&apos;area amministrativa, logging degli accessi.
          </P>
        </Section>

        <Section title="7. Destinatari dei dati e trasferimenti">
          <P>
            I dati possono essere comunicati a soggetti ai quali la
            comunicazione sia necessaria per il perseguimento delle
            finalità di cui al punto 5, in particolare:
          </P>
          <ul className="ml-5 mt-2 list-disc space-y-1.5">
            <li>
              <strong>SolarLead</strong> (Responsabile ex art. 28 GDPR)
              per la fornitura della piattaforma tecnologica;
            </li>
            <li>
              <strong>Fornitori cloud</strong> (Supabase, Amazon Web
              Services, Vercel) con server localizzati in UE/SEE;
            </li>
            <li>
              <strong>Fornitori di servizi di intelligenza artificiale</strong>{' '}
              per OCR bolletta (Anthropic Claude Vision) e rendering
              immagini/video (Replicate), con accordi di trattamento
              dati che prevedono <em>zero retention</em> sui contenuti
              processati;
            </li>
            <li>
              <strong>Provider di posta elettronica</strong> (Postmark,
              SendGrid o equivalenti) per l&apos;invio di comunicazioni
              transazionali e di marketing;
            </li>
            <li>
              <strong>CRM dell&apos;impresa installatrice</strong>:
              qualora il Titolare abbia configurato un&apos;integrazione,
              le informazioni raccolte tramite il form sopralluogo
              possono essere trasmesse al sistema gestionale (HubSpot,
              Pipedrive, Salesforce o equivalenti) per la presa in
              carico della richiesta.
            </li>
          </ul>
          <P className="mt-3">
            <strong>Trasferimenti extra-UE</strong>: alcuni fornitori
            (in particolare quelli di intelligenza artificiale) possono
            comportare un trasferimento dei dati negli Stati Uniti
            d&apos;America. In tali casi il trasferimento avviene sulla
            base delle <em>Standard Contractual Clauses</em> approvate
            dalla Commissione Europea (Decisione 2021/914) o, ove
            applicabile, della certificazione{' '}
            <em>EU-US Data Privacy Framework</em>.
          </P>
          <P>
            I dati <strong>non vengono ceduti o venduti</strong> a soggetti
            terzi per finalità di profilazione commerciale autonoma.
          </P>
        </Section>

        <Section title="8. Periodo di conservazione">
          <P>
            I dati sono conservati per il tempo strettamente necessario
            al perseguimento delle finalità per le quali sono stati
            raccolti, e in ogni caso secondo i seguenti criteri:
          </P>
          <ul className="ml-5 mt-2 list-disc space-y-1.5">
            <li>
              <strong>Lead non convertiti</strong>: massimo{' '}
              <strong>24 mesi</strong> dall&apos;ultimo contatto utile,
              salvo opposizione al trattamento;
            </li>
            <li>
              <strong>Lead che hanno richiesto sopralluogo</strong>:
              massimo <strong>5 anni</strong> dalla chiusura della
              trattativa, per finalità di gestione contrattuale e
              difesa in giudizio;
            </li>
            <li>
              <strong>Dati di navigazione (eventi portale)</strong>:
              massimo <strong>13 mesi</strong>;
            </li>
            <li>
              <strong>Comunicazioni email inviate</strong>: massimo{' '}
              <strong>24 mesi</strong> ai fini di reportistica e
              tracciamento deliverability;
            </li>
            <li>
              <strong>Dati cancellati su richiesta</strong>: rimossi
              entro <strong>30 giorni</strong> dalla ricezione della
              richiesta, fatti salvi obblighi legali di conservazione
              (es. obblighi fiscali decennali).
            </li>
          </ul>
        </Section>

        <Section title="9. Diritti dell'interessato">
          <P>
            In qualità di interessati, ai sensi degli artt. 15-22 GDPR,
            avete il diritto di:
          </P>
          <ul className="ml-5 mt-2 list-disc space-y-1.5">
            <li>
              <strong>Accedere</strong> ai vostri dati personali e
              ottenerne copia (art. 15);
            </li>
            <li>
              Richiedere la <strong>rettifica</strong> dei dati inesatti
              o l&apos;integrazione dei dati incompleti (art. 16);
            </li>
            <li>
              Ottenere la <strong>cancellazione</strong> dei dati
              («diritto all&apos;oblio»), nei limiti previsti dalla
              normativa (art. 17);
            </li>
            <li>
              Richiedere la <strong>limitazione</strong> del trattamento
              (art. 18);
            </li>
            <li>
              Ricevere i dati che ci avete fornito in un formato
              strutturato, di uso comune e leggibile da dispositivo
              automatico (<strong>portabilità</strong>, art. 20);
            </li>
            <li>
              <strong>Opporsi</strong> in qualsiasi momento, per motivi
              legati alla vostra situazione particolare, al trattamento
              che si basa sul legittimo interesse, ivi compreso il
              marketing diretto (art. 21);
            </li>
            <li>
              Non essere sottoposti a <strong>decisioni automatizzate</strong>{' '}
              che producano effetti giuridici significativi (art. 22).
              La piattaforma SolarLead non adotta decisioni interamente
              automatizzate sul lead;
            </li>
            <li>
              <strong>Revocare il consenso</strong> in qualsiasi momento,
              senza pregiudicare la liceità del trattamento basato sul
              consenso prima della revoca.
            </li>
          </ul>
        </Section>

        <Section title="10. Modalità di esercizio dei diritti">
          <P>
            Per esercitare i diritti elencati al punto 9 potete:
          </P>
          <ul className="ml-5 mt-2 list-disc space-y-1.5">
            <li>
              Scrivere all&apos;indirizzo email del Titolare indicato nel
              footer del portale o nelle email ricevute;
            </li>
            <li>
              Scrivere all&apos;indirizzo email di supporto privacy della
              Piattaforma:{' '}
              <a href="mailto:privacy@solarlead.it" className="underline">
                privacy@solarlead.it
              </a>
              ;
            </li>
            <li>
              Cliccare sul link <em>«Non voglio più ricevere comunicazioni»</em>{' '}
              presente in ogni email e in fondo alla pagina dossier per
              esercitare immediatamente il diritto di opposizione al
              marketing diretto.
            </li>
          </ul>
          <P className="mt-3">
            La risposta verrà fornita senza ingiustificato ritardo e
            comunque entro <strong>30 giorni</strong> dalla ricezione
            della richiesta. Tale termine può essere prorogato di
            ulteriori 60 giorni se necessario, tenuto conto della
            complessità o del numero di richieste.
          </P>
        </Section>

        <Section title="11. Reclamo all'Autorità di controllo">
          <P>
            Qualora riteniate che il trattamento dei vostri dati
            personali avvenga in violazione del GDPR, avete il diritto
            di proporre reclamo all&apos;Autorità di controllo competente,
            ai sensi dell&apos;art. 77 GDPR.
          </P>
          <P>
            In Italia l&apos;Autorità di controllo è il{' '}
            <strong>Garante per la protezione dei dati personali</strong>:
          </P>
          <ul className="ml-5 mt-2 list-disc space-y-1">
            <li>
              Sito web:{' '}
              <a
                href="https://www.garanteprivacy.it"
                target="_blank"
                rel="noopener noreferrer"
                className="underline"
              >
                www.garanteprivacy.it
              </a>
            </li>
            <li>Email: protocollo@gpdp.it</li>
            <li>Sede: Piazza Venezia, 11 — 00187 Roma</li>
          </ul>
        </Section>

        <Section title="12. Natura del conferimento dei dati">
          <P>
            Il conferimento dei dati identificativi e di contatto
            (nome, telefono, email) all&apos;interno del form sopralluogo
            è <strong>necessario</strong> per dare seguito alla vostra
            richiesta. Il mancato conferimento renderà impossibile per
            il Titolare contattarvi.
          </P>
          <P>
            Il caricamento della bolletta è invece <strong>facoltativo</strong>{' '}
            e serve esclusivamente ad affinare la stima del risparmio.
            Potete consultare la pagina dossier senza caricare alcun
            documento.
          </P>
        </Section>

        <Section title="13. Cookie e tecnologie analoghe">
          <P>
            La pagina del dossier utilizza esclusivamente{' '}
            <strong>cookie tecnici</strong> di sessione, necessari al
            corretto funzionamento del portale. Non vengono utilizzati
            cookie di profilazione di terze parti né strumenti di
            tracciamento pubblicitario cross-site.
          </P>
          <P>
            Per finalità di analisi statistica e ottimizzazione possono
            essere registrati eventi di interazione anonimi (apertura
            pagina, clic sui CTA, durata sessione) collegati al{' '}
            <em>slug</em> univoco del dossier. Tali eventi non
            costituiscono profilazione individuale ex art. 22 GDPR.
          </P>
        </Section>

        <Section title="14. Modifiche all'Informativa">
          <P>
            La presente Informativa può essere aggiornata per riflettere
            modifiche normative, evoluzioni tecnologiche o cambiamenti
            organizzativi. La versione vigente è sempre disponibile a
            questo indirizzo, con indicazione della data di ultimo
            aggiornamento in apertura.
          </P>
          <P>
            In caso di modifiche sostanziali alle modalità di trattamento,
            ne sarà data comunicazione attraverso i canali abituali (email
            o avviso nel portale).
          </P>
        </Section>

        <hr className="my-10 border-outline-variant" />

        <div className="rounded-2xl bg-surface-container-low p-5 text-sm text-on-surface-variant">
          <p className="font-medium text-on-surface">Riepilogo rapido</p>
          <ul className="ml-5 mt-2 list-disc space-y-1">
            <li>I tuoi dati servono a generare la proposta e contattarti.</li>
            <li>Non vendiamo i tuoi dati a terzi.</li>
            <li>
              Puoi cancellarti in qualsiasi momento dal link «Non voglio più
              ricevere comunicazioni» o scrivendo a{' '}
              <a
                href="mailto:privacy@solarlead.it"
                className="underline"
              >
                privacy@solarlead.it
              </a>
              .
            </li>
            <li>
              I dati sono conservati al massimo 24 mesi (lead non convertiti)
              o 5 anni (trattativa avviata).
            </li>
            <li>
              Per reclami puoi rivolgerti al Garante Privacy:{' '}
              <a
                href="https://www.garanteprivacy.it"
                target="_blank"
                rel="noopener noreferrer"
                className="underline"
              >
                garanteprivacy.it
              </a>
              .
            </li>
          </ul>
        </div>

        <p className="mt-10 text-xs text-on-surface-variant">
          Questa informativa è redatta in conformità al Regolamento (UE)
          2016/679 («GDPR»), al D.Lgs. 30 giugno 2003 n. 196 («Codice
          Privacy») come modificato dal D.Lgs. 10 agosto 2018 n. 101, e
          alle linee guida dell&apos;EDPB e del Garante italiano.
        </p>
      </article>

      <footer className="border-t border-outline-variant bg-surface-container">
        <div className="mx-auto max-w-3xl px-6 py-6 text-xs text-on-surface-variant">
          <Link href="/" className="underline">
            Torna alla home
          </Link>
        </div>
      </footer>
    </main>
  );
}

// ---------------------------------------------------------------------------

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-8">
      <h2 className="font-headline text-xl font-semibold tracking-tighter text-on-surface md:text-2xl">
        {title}
      </h2>
      <div className="mt-3 space-y-3 text-on-surface-variant">{children}</div>
    </section>
  );
}

function P({ children, className }: { children: React.ReactNode; className?: string }) {
  return <p className={className ?? ''}>{children}</p>;
}
