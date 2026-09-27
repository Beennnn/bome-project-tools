"""Eprouver une configuration Bome contre du MIDI reel.

POURQUOI CET OUTIL EXISTE. Une configuration Bome est une machine a etats muette :
rien ne dit si un translator a tire, et un translator qui ne matche pas ne se plaint
pas -- il ne fait rien, silencieusement, pour tout le concert. Le seul verdict est
d'envoyer un message et de regarder ce qui sort.

LA LECON QUI A MOTIVE L'ECRITURE, payee le 2026-09-27 : un test ecrit a la main a
rendu 0 sur 6 et fait croire a une regression. La configuration etait juste ; le test
visait le canal 11 alors que les declencheurs ecoutent le 12. Deux gardes en
decoulent, et ce sont les seules qui comptent vraiment :

  - `attendu_vide` force a declarer qu'on attend le silence, pour qu'un test muet
    parce qu'il rate sa cible ne puisse plus passer pour un test reussi ;
  - `sonde_declencheur` lit le canal et le numero DANS le fichier de configuration
    au lieu de les retaper. Un plan de test ne repete jamais ce que la config dit
    deja -- c'est ainsi qu'il se desynchronise.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

try:
    import mido
except ImportError:                                    # pragma: no cover
    mido = None


# ── lire les declencheurs depuis la configuration ───────────────────────────────

@dataclass
class Declencheur:
    """Ce qu'un translator ECOUTE, tel que le fichier le declare."""
    preset: int
    index: int
    nom: str
    port: str
    type: str          # ControlChange, NoteOn, ProgramChange…
    canal: int | None  # 1-16, ou None pour « n'importe quel canal »
    numero: int | None # numero de CC ou de note, None si la valeur est variable


_TYPE_MIDO = {"ControlChange": "control_change", "NoteOn": "note_on",
              "NoteOff": "note_off", "ProgramChange": "program_change",
              "PitchBend": "pitchwheel", "ChannelPressure": "aftertouch"}


def declencheurs(chemin: Path) -> list[Declencheur]:
    """Tous les entrants MIDI d'un projet, canal et numero compris.

    Le canal est 0-BASED dans le fichier -- `num="11"` est le canal 12. C'est le
    piege qui a coute un faux diagnostic, et la raison pour laquelle un plan de test
    doit lire ici plutot que de retaper des numeros a la main.
    """
    texte = chemin.read_text(encoding="utf-8", errors="replace")
    out: list[Declencheur] = []
    for m in re.finditer(r"^\[Preset\.(\d+)\]$", texte, re.M):
        deb = m.end()
        fin = texte.find("\n[Preset.", deb)
        corps = texte[deb:fin if fin > 0 else len(texte)]
        if (a := re.search(r"^Active=(\d)", corps, re.M)) and a.group(1) != "1":
            continue                                   # un preset inactif ne tire pas
        noms = dict(re.findall(r"^Name(\d+)=(.*)$", corps, re.M))
        for mi in re.finditer(r"^Incoming(\d+)=MID3(.*)$", corps, re.M):
            i, xml = mi.group(1), mi.group(2)
            port = (p.group(1) if (p := re.search(r"<Port>([^<]+)</Port>", xml)) else "")
            typ = (t.group(1) if (t := re.search(r'Simple Type="(\w+)"', xml)) else "")
            ch = re.search(r'<Channel num="(\d+)"(\s+Type="Any")?', xml)
            num = re.search(r'<Value1 num="(0x[0-9A-Fa-f]+|\d+)"', xml)
            out.append(Declencheur(
                preset=int(m.group(1)), index=int(i), nom=noms.get(i, ""),
                port=port, type=typ,
                canal=None if (ch and ch.group(2)) else (int(ch.group(1)) + 1 if ch else None),
                numero=(int(num.group(1), 16) if num and num.group(1).startswith("0x")
                        else int(num.group(1)) if num else None)))
    return out


def sonde_declencheur(decls: list[Declencheur], preset: int, numero: int):
    """Le declencheur d'un preset pour un numero donne -- canal lu dans la config."""
    for d in decls:
        if d.preset == preset and d.numero == numero:
            return d
    raise LookupError(f"aucun declencheur de numero {numero} dans le preset {preset}")


# ── envoyer, ecouter, comparer ──────────────────────────────────────────────────

@dataclass
class Cas:
    """Un cas de test : ce qu'on envoie, ce qu'on attend en retour.

    `attendu` est un ensemble de (canal, numero) observes en sortie. `attendu_vide`
    doit etre mis a True pour attendre le silence : sans lui, un cas qui n'observe
    rien est considere comme RATE, parce que c'est presque toujours la cible qu'on a
    manquee plutot que le silence qu'on voulait.
    """
    titre: str
    envoi: object                       # un mido.Message
    attendu: set = field(default_factory=set)
    attendu_vide: bool = False
    filtre: object = None               # quels messages retenir en sortie
    attente_s: float = 0.5


class Sonde:
    """Ouvre un port en lecture et en ecriture, et joue des cas."""

    def __init__(self, port: str):
        if mido is None:
            raise SystemExit("il manque mido : pip install mido python-rtmidi")
        self.port = port
        self._vu: list = []
        self._stop = threading.Event()

    def __enter__(self):
        self._out = mido.open_output(self.port)
        self._t = threading.Thread(target=self._ecoute, daemon=True)
        self._t.start()
        time.sleep(0.4)                 # laisser le port s'etablir avant le 1er envoi
        return self

    def __exit__(self, *_):
        self._stop.set()
        self._out.close()

    def _ecoute(self):
        with mido.open_input(self.port) as inp:
            while not self._stop.is_set():
                for m in inp.iter_pending():
                    self._vu.append(m)
                time.sleep(0.01)

    def joue(self, cas: Cas) -> tuple[bool, set]:
        self._vu.clear()
        self._out.send(cas.envoi)
        time.sleep(cas.attente_s)
        garde = cas.filtre or (lambda m: m.type == "control_change")
        obs = {(m.channel + 1, getattr(m, "control", getattr(m, "note", None)))
               for m in list(self._vu) if garde(m)}
        # Le message qu'on vient d'envoyer nous revient : un port virtuel boucle.
        obs.discard((cas.envoi.channel + 1,
                     getattr(cas.envoi, "control", getattr(cas.envoi, "note", None))))
        return (not obs if cas.attendu_vide else obs == cas.attendu), obs


def deroule(port: str, cas: list[Cas]) -> int:
    """Joue tous les cas, imprime un tableau, rend le nombre d'echecs."""
    print("%-38s %-16s %-16s %s" % ("cas", "attendu", "observe", ""))
    print("-" * 82)
    echecs = 0
    with Sonde(port) as s:
        for c in cas:
            ok, obs = s.joue(c)
            echecs += not ok
            att = "silence" if c.attendu_vide else str(sorted(c.attendu))
            print("%-38s %-16s %-16s %s" % (c.titre[:38], att[:16],
                                            (str(sorted(obs)) or "rien")[:16],
                                            "OK" if ok else "!! ECART"))
    print("-" * 82)
    print("%d cas · %d ecart(s)" % (len(cas), echecs))
    return echecs
