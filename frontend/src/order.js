// SPDX-FileCopyrightText: 2026 Kasa Panel contributors
//
// SPDX-License-Identifier: GPL-3.0-or-later
//
// SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
// see docs/ai-bom.spdx.json for the AI usage declaration.

// Where each card sits on the panel.
//
// The saved order is a list of device identifiers, which is not the same
// thing as the list of devices: a device may have been added since the
// order was saved, or removed and still named in it.  So the order is
// treated as a seating plan rather than a copy of the inventory.  A
// device the plan knows takes its place; one it does not know goes at
// the end, where a new device is noticed; a name with no device is
// simply nobody, and is left in the plan in case the device comes back.

// Arranges devices by a saved order, newcomers last.
export function orderDevices(devices, order) {
  const list = devices || [];
  const places = new Map((order || []).map((id, index) => [id, index]));
  const seated = list.filter((device) => places.has(device.device_id))
    .sort((a, b) => places.get(a.device_id) - places.get(b.device_id));
  const rest = list.filter((device) => !places.has(device.device_id));
  return [...seated, ...rest];
}

// Moves one card into another's place.  Dropping a card to the right of
// where it was puts it after the card it was dropped on, and to the
// left puts it before: either way it lands where the card it was
// dropped on was standing, which is what the hand expects.
export function moveCard(ids, moved, target) {
  const from = ids.indexOf(moved);
  const to = ids.indexOf(target);
  if (from < 0 || to < 0 || from === to) {
    return ids.slice();
  }
  const next = ids.slice();
  next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}

// Moves one card a step along the row, for the keyboard.  A card at
// either end stays where it is rather than wrapping around.
export function shiftCard(ids, moved, step) {
  const from = ids.indexOf(moved);
  if (from < 0) {
    return ids.slice();
  }
  const to = Math.min(ids.length - 1, Math.max(0, from + step));
  return moveCard(ids, moved, ids[to]);
}
