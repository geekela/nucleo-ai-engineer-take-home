# case-001 viewer: UI and UX pass

Follow-up to `2026-11-24-ct-mask-viewer-design.md`. The first build met the brief; this pass makes it
easier to read and use during a live demo.

## Who it is for

Someone inspecting one model output on one abdominal CT, on a shared screen: does the lesion mask match
the reference, where does it differ, and what happened to each upload, including the deliberately broken
ones.

## Problems with the first build

- Model fill (red) and reference fill (gold) blended into the same orange where they overlapped, so the
  comparison the brief asks for was hard to see.
- Dice was a line inside the upload card, and the card didn't say which upload the layers showed.
- Nothing helped you find the slices with lesions.
- "Service busy, retrying in N s" never counted down; a run waiting for a free slot after a retry still
  said "retrying"; a 30 MB upload looked frozen for 20 to 40 s.
- No way to cancel an upload or to clear a list full of failed test uploads.

## Design

**Colour belongs to the masks.** The chrome is greyscale (graphite `#1C2127`, panel `#242A32`, rules
`#363E48`, text `#E9EDF1`, secondary `#9AA4B0`), and the image sits in a true-black well, as on a
reading-room screen. Every hue therefore means a mask: amber `#FF9E3D` = model, sky `#5CB8F0` =
reference, teal `#1FB89A` = both. Red and yellow are kept for errors and warnings. The colours keep
their meaning across views.

**Model filled, reference outlined.** Where the two overlap you still see both, and the difference is
shape, not just hue, which also helps colour-blind readers. The Agreement view instead colours each
voxel by who marked it (model only, both, reference only).

**Type:** Public Sans (USWDS). It has plain zeros (Atkinson Hyperlegible's slashed zero reads as Ø, the
diameter sign, in a medical UI), tabular figures for every number, and sentence-case headings with no
all-caps labels.

**Layout:** header (case, scan facts, the two upload actions), viewer column, sidebar, status bar.

```
case-001  Abdomen CT, 180 axial slices…                   [Upload files…] [Upload case-001]
┌────────────────────────────────────────────┐ Model vs reference
│Slice 91 of 180   A            Soft tissue  │  0.955  Dice …
│z 449.9 mm                       W 400 L 40 │  ▬▬▬▬▬▬▬▬▬▬▬▬ model only / both / reference only
│R                                          L│  volumes · lesions found · grid check
│                    P                       │ Layers  [Overlay|Agreement]  switches  opacity
└────────────────────────────────────────────┘ Uploads (n)                     Clear failed
 Model     ▁▂▅▇█▇▅▃▂▁  (per-slice lesion volume) ✓ case-001-dicom.zip   Done  DSC 0.955
 Reference ▔▀▀██▀▀▔▔                              ✕ photo.png  That file isn't a valid zip…
 ───────────●──────── slider                     ◌ busy.zip   Service busy. Retrying in 2 s
 [Soft tissue|Liver|Bone|Lung]
Segmentation service  host  Token set                            Data credit
```

**The one bold element: the slice profile.** Under the image, a mirrored bar chart shows the lesion
voxels on each slice, model growing up and reference growing down, on one shared scale. Click or drag
it to jump to a slice. It shows at a glance where lesion volume sits along the scan and where the two
masks disagree.

**Viewer vernacular:** the corner annotations (slice, z position, window) and the patient-side letters
come from the DICOM orientation, not hard-coded. There are four window presets (soft tissue, liver,
bone, lung). The wheel moves one slice per mouse notch, and trackpad movement accumulates.

## Behaviour changes that came with it

- Uploads stream with progress ("Uploading, 46%"), then show "Segmenting, 6 s" once every byte is
  sent, then a live countdown while the service asks us to wait.
- Cancel an upload in progress, clear failed uploads, upload several files at once, or drop them on
  the page. Files over 150 MB are refused before anything is sent.
- The newest finished upload is selected automatically, and the comparison panel names the upload it
  shows.
- Lesion counts are 3D connected components (26-connected): how many reference lesions the model
  touches, and how many model lesions touch no reference lesion.
- A failed CT load offers "Try again". Missing data says to restart after adding the file.

## Quality floor

Keyboard: every control is a native input (radio groups, switches, range sliders) with a visible focus
ring. The layout is responsive down to 390 px, and reduced motion turns the spinners off. An axe scan in
the end-to-end suite has to report no serious or critical issues.
