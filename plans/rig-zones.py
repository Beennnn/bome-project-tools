#!/usr/bin/env python3
"""Plan de test du rig a zones : ce qui se verifie sans toucher au clavier.

CE QUE CE PLAN COUVRE, et pourquoi c'est borne. Un translator ne se teste qu'en
envoyant son entrant et en regardant ce qui sort -- il faut donc pouvoir ECRIRE sur
le port qu'il ecoute. Cinq presets ecoutent un port materiel, et rien ne permet d'y
injecter un message depuis un script :

    preset                          port ecoute               testable
    Touche CC & Bend  (inactif)     TOUCHE Main Port          non
    Breath Control                  Breath Controller 5.2     non
    Roli Keyb Notes   (inactif)     Seaboard RISE 49          non
    Degraded mode                   P-Series + Rig Bus        partiellement
    Clavier                         P-Series                  non
    Zones notes                     P-Series                  non  <- le coeur du rig
    Zones etat                      Rig Bus                   OUI
    Zones controleurs               Rig Bus                   OUI
    Ableton to Stage Traxx          Rig Bus                   OUI
    Clock beat                      tous                      OUI

« Zones notes » est le plus important et le moins testable : c'est lui qui envoie une
note dans la bonne zone selon sa hauteur. Il exige de jouer sur le P-Series, donc une
main humaine -- la section B du plan, a derouler avec l'observateur.

LE PIEGE A NE PLUS REJOUER. Le canal est 0-BASED dans le fichier de configuration :
`<Channel num="11"/>` est le canal 12. Un plan de test qui retape des numeros se
desynchronise de la configuration ; celui-ci les LIT, par sonde_declencheur.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mido                                                   # noqa: E402
from bome_project.probe import Cas, declencheurs, deroule, sonde_declencheur  # noqa: E402

CONFIG = Path.home() / "dev/music/rig-private/bome/Ben_config.bmtp"
PORT = "Rig Bus"
ZONES_ETAT, ZONES_CTRL = 6, 8

# Quelles zones chaque declencheur doit couper. C'est le CONTRAT, ecrit ici en clair
# parce qu'un test qui deduit l'attendu de la configuration ne teste plus rien.
PANIC = {112: {1}, 113: {1},           # bornes propres a une zone
         116: {2}, 122: {2},
         120: {3}, 121: {3},
         114: {1, 2},                  # bornes PARTAGEES : elles separent deux zones,
         115: {2, 3},                  #   donc deplacer l'une orpheline des notes des deux cotes
         102: {1, 2, 3}}               # tone global : transpose tout, coupe tout


def cas_panic(decls) -> list[Cas]:
    """A · le panic : une borne bouge, les zones concernees se taisent."""
    out = []
    for cc, zones in PANIC.items():
        d = sonde_declencheur(decls, ZONES_ETAT, cc)
        out.append(Cas(
            titre=f"CC{cc} ch{d.canal} → panic {sorted(zones)}",
            envoi=mido.Message("control_change", channel=d.canal - 1, control=cc, value=60),
            attendu={(z, 123) for z in zones},
            filtre=lambda m: m.type == "control_change" and m.control == 123))
    return out


def cas_controleurs(decls) -> list[Cas]:
    """B · les controleurs : un CC du clavier maitre part vers les trois zones.

    Le fan-out n'est PAS factorise par un Perform, et c'est correct : ici les trois
    destinations recoivent toujours, sans condition. Un Perform n'y gagnerait rien --
    il n'economise que les croisements d'un produit, et il n'y a pas de produit.
    """
    d = sonde_declencheur(decls, ZONES_CTRL, 1)               # CC1 modulation
    return [Cas(
        titre=f"CC1 ch{d.canal} modulation → 3 zones",
        envoi=mido.Message("control_change", channel=d.canal - 1, control=1, value=64),
        attendu={(1, 1), (2, 1), (3, 1)},
        filtre=lambda m: m.type == "control_change" and m.control == 1)]


def cas_garde(decls) -> list[Cas]:
    """C · les gardes : ce qui NE doit rien produire.

    Sans ces cas, un plan qui rate sa cible rend « rien » partout et se lit comme un
    succes. C'est exactement ce qui est arrive le 2026-09-27 avec un mauvais canal.
    """
    d = sonde_declencheur(decls, ZONES_ETAT, 112)
    return [
        Cas(titre=f"CC112 sur le MAUVAIS canal {d.canal - 1} → silence",
            envoi=mido.Message("control_change", channel=d.canal - 2, control=112, value=60),
            attendu_vide=True,
            filtre=lambda m: m.type == "control_change" and m.control == 123),
        Cas(titre="CC 99 inconnu → silence",
            envoi=mido.Message("control_change", channel=d.canal - 1, control=99, value=60),
            attendu_vide=True,
            filtre=lambda m: m.type == "control_change" and m.control == 123),
        Cas(titre="NoteOn E3 → silence, le preset de test est parti",
            envoi=mido.Message("note_on", channel=0, note=0x40, velocity=100),
            attendu_vide=True,
            filtre=lambda m: m.type == "control_change" and m.control == 123),
    ]


def main() -> int:
    decls = declencheurs(CONFIG)
    print(f"{CONFIG.name} · {len(decls)} entrants MIDI dans les presets actifs\n")
    total = 0
    for titre, cas in (("A · PANIC", cas_panic(decls)),
                       ("B · CONTROLEURS", cas_controleurs(decls)),
                       ("C · GARDES — le silence attendu", cas_garde(decls))):
        print(f"╔═ {titre} " + "═" * (60 - len(titre)))
        total += deroule(PORT, cas)
        print()
    print("=" * 82)
    print("VERDICT :", "tout conforme" if not total else f"{total} ecart(s) a examiner")
    print("""
NON COUVERT, faute de pouvoir ecrire sur un port materiel — a derouler a la main :
  1. Zones notes · jouer une note dans chaque zone du clavier, verifier qu'elle
     sort sur le canal 1, 2 puis 3 selon la hauteur.
  2. Breath Control · souffle, morsure, hochement, inclinaison au TEControl.
  3. Clavier · les CC du P-Series.
  4. Degraded mode · debrancher Ableton et verifier que le clavier sonne encore.""")
    return total


if __name__ == "__main__":
    raise SystemExit(main())
