// SPDX-FileCopyrightText: 2026 Kasa Panel contributors
//
// SPDX-License-Identifier: GPL-3.0-or-later
//
// SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
// see docs/ai-bom.spdx.json for the AI usage declaration.

import assert from 'node:assert/strict';
import {after, describe, it} from 'node:test';
import {findByText, mount, text} from './harness.mjs';

async function signIn(app, password = 'secret') {
  const doc = app.document;
  const user = doc.querySelector('input[name="username"]');
  const pass = doc.querySelector('input[name="password"]');
  set(app.window, user, 'ada');
  set(app.window, pass, password);
  doc.querySelector('form.plate button[type="submit"]').click();
  await app.settle();
}

// React listens for input events, so the value has to be set the way a
// browser sets it rather than by assignment alone.
function set(window, node, value) {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, 'value').set;
  setter.call(node, value);
  node.dispatchEvent(new window.Event('input', {bubbles: true}));
}

describe('sign in', () => {
  it('shows the sign-in plate first', async () => {
    const app = await mount();
    after(() => app.close());
    assert.ok(app.document.querySelector('form.plate'),
      'the sign-in form should be on screen');
    assert.equal(app.document.querySelector('.shell'), null);
    assert.deepEqual(app.errors, []);
  });

  it('reaches the shell and stays there', async () => {
    const app = await mount();
    after(() => app.close());
    await signIn(app);
    assert.ok(app.document.querySelector('.shell'),
      'the shell should replace the sign-in form');
    assert.equal(app.document.querySelector('form.plate'), null,
      'the sign-in form should be gone');
    assert.match(text(app.document.querySelector('.rail-foot')), /ada/);
    assert.deepEqual(app.errors, []);
  });

  it('shows both versions beside the sign-out button', async () => {
    const app = await mount();
    after(() => app.close());
    await signIn(app);
    const row = app.document.querySelector('.rail-foot-row');
    assert.ok(findByText(row, 'button', 'Sign out'),
      'the versions should share a row with the sign-out button');
    const versions = text(row.querySelector('.versions'));
    assert.match(versions, /kasapanel 1\.8\.1/);
    assert.match(versions, /python-kasa 0\.10\.2/);
  });

  // The bug this suite exists for: a browser that refuses to keep the
  // Secure cookie, which is every browser behind a self-signed
  // certificate.  The panel has to work on the header alone.
  it('works when the browser drops the session cookie', async () => {
    const app = await mount({acceptCookie: false});
    after(() => app.close());
    await signIn(app);
    assert.ok(app.document.querySelector('.shell'),
      'no cookie must not mean no panel');
    const dashboard = app.server.calls.filter(
      (call) => call.pathname === '/api/dashboard');
    assert.ok(dashboard.length > 0, 'the dashboard should have been read');
    assert.equal(dashboard[0].headers['X-Kasa-Session'], app.server.token,
      'the session should travel in the header');
  });

  it('keeps a bad password on the sign-in page', async () => {
    const app = await mount();
    after(() => app.close());
    await signIn(app, 'wrong');
    assert.ok(app.document.querySelector('form.plate'));
    assert.match(text(app.document.querySelector('.alarm')),
      /did not match/);
  });
});

describe('the panel', () => {
  it('draws a card per device with a working rocker', async () => {
    const app = await mount();
    after(() => app.close());
    await signIn(app);
    const card = app.document.querySelector('.card');
    assert.ok(card, 'a device card should be drawn');
    const rocker = card.querySelector('.rocker');
    assert.ok(rocker, 'the card should carry a rocker switch');

    rocker.click();
    await app.settle();
    const sent = app.server.calls.find(
      (call) => call.pathname.endsWith('/action'));
    assert.ok(sent, 'the rocker should send an action');
    assert.equal(sent.headers['X-Kasa-CSRF'], app.server.csrf,
      'writes must carry the CSRF token');
  });

  it('offers every snooze length from a menu beside the LED', async () => {
    const app = await mount();
    after(() => app.close());
    await signIn(app);
    const card = app.document.querySelector('.card');
    const toggle = findByText(card, '.card-actions button', 'Snooze');
    assert.ok(toggle, 'the card should carry a snooze menu');
    assert.equal(card.querySelector('.menu'), null,
      'the menu should start closed');
    toggle.click();
    await app.settle();

    const items = [...card.querySelectorAll('.menu [role="menuitem"]')]
      .map(text);
    assert.deepEqual(items, ['5 minutes', '15 minutes', '30 minutes',
      '1 hour', '3 hours', '6 hours', '12 hours', '1 day', '1 week',
      'Custom…']);

    findByText(card, '.menu button', '3 hours').click();
    await app.settle();
    const sent = app.server.calls.find(
      (call) => call.pathname.endsWith('/snooze'));
    assert.ok(sent, 'choosing a length should reach the daemon');
    assert.equal(sent.method, 'POST');
    assert.equal(sent.body.for, '3h');
    assert.equal(sent.headers['X-Kasa-CSRF'], app.server.csrf);
    assert.equal(card.querySelector('.menu'), null,
      'the menu should close after a choice');
    assert.match(text(card.querySelector('.snoozed')), /2026-07-29 16:55/);

    // Once snoozed, the menu leads with the way back.
    findByText(card, '.card-actions button', 'Snoozed').click();
    await app.settle();
    findByText(card, '.menu button', 'Resume schedule').click();
    await app.settle();
    const lifted = app.server.calls.find(
      (call) => call.method === 'DELETE' && call.pathname.endsWith('/snooze'));
    assert.ok(lifted, 'resuming should reach the daemon');
    assert.equal(card.querySelector('.snoozed'), null);
    assert.deepEqual(app.errors, []);
  });

  it('takes a custom snooze in systemd.time syntax', async () => {
    const app = await mount();
    after(() => app.close());
    await signIn(app);
    const card = app.document.querySelector('.card');
    findByText(card, '.card-actions button', 'Snooze').click();
    await app.settle();
    findByText(card, '.menu button', 'Custom').click();
    await app.settle();

    const dialog = app.document.querySelector('[role="dialog"]');
    assert.ok(dialog, 'Custom should open a dialog');
    assert.match(text(dialog), /systemd\.time/);
    const examples = [...dialog.querySelectorAll('.examples button')]
      .map(text);
    assert.ok(examples.includes('tomorrow 07:00'),
      'the dialog should show worked examples');
    const input = dialog.querySelector('input[name="snooze"]');
    const submit = findByText(dialog, 'button[type="submit"]', 'Snooze');

    // A mistake is explained before anything is sent.
    set(app.window, input, 'soon');
    await new Promise((resolve) => app.window.setTimeout(resolve, 300));
    await app.settle();
    assert.match(text(dialog.querySelector('.snooze-check')),
      /not a time span/);
    assert.ok(submit.disabled, 'a bad time must not be submittable');

    // An example fills the field, and the preview says when.
    findByText(dialog, '.examples button', 'tomorrow 07:00').click();
    await new Promise((resolve) => app.window.setTimeout(resolve, 300));
    await app.settle();
    assert.equal(input.value, 'tomorrow 07:00');
    assert.match(text(dialog.querySelector('.snooze-check')),
      /Resumes 2026-07-30 07:00/);

    submit.click();
    await app.settle();
    const sent = app.server.calls.find(
      (call) => call.method === 'POST' && call.pathname.endsWith('/snooze'));
    assert.equal(sent.body.for, 'tomorrow 07:00');
    assert.equal(app.document.querySelector('[role="dialog"]'), null,
      'the dialog should close once the snooze is set');
    assert.deepEqual(app.errors, []);
  });

  it('shows the host clock to the second, with its zone', async () => {
    const app = await mount();
    after(() => app.close());
    await signIn(app);
    const readout = findByText(app.document, '.readout', 'Host clock');
    assert.match(text(readout), /14:58:0\d JST/,
      'the host clock should carry seconds and the daemon zone');
  });

  // The daemon in the fixtures keeps Tokyo time, so unless this machine
  // does too, the viewer is told what that instant is where they are.
  it('adds the browser clock when the zones differ', async () => {
    const app = await mount();
    after(() => app.close());
    await signIn(app);
    const readout = findByText(app.document, '.readout', 'Host clock');
    const here = readout.querySelector('.clock-here');
    const instant = new Date('2026-07-29T14:58:05+09:00');
    if (-instant.getTimezoneOffset() === 540) {
      assert.equal(here, null, 'the same zone needs no aside');
      return;
    }
    assert.ok(here, 'a browser in another zone should be told so');
    const shown = text(here);
    assert.match(shown, /^\(\d{2}:\d{2}:\d{2} .+\)$/,
      'the aside is a time and a zone in parentheses');
    const expected = new Intl.DateTimeFormat(undefined, {
      hour: '2-digit', minute: '2-digit', second: '2-digit',
      hourCycle: 'h23', timeZoneName: 'short',
    }).format(instant);
    assert.equal(shown, `(${expected})`,
      'the aside should be the same instant, read here');
  });

  it('leaves the browser clock out when the zones agree', async () => {
    const app = await mount({hostZone: 'local'});
    after(() => app.close());
    await signIn(app);
    const readout = findByText(app.document, '.readout', 'Host clock');
    assert.equal(readout.querySelector('.clock-here'), null,
      'repeating the same reading twice tells nobody anything');
    assert.match(text(readout), /14:58:0\d/);
  });

  it('fills the readouts from the dashboard', async () => {
    const app = await mount();
    after(() => app.close());
    await signIn(app);
    const readouts = text(app.document.querySelector('.readouts'));
    assert.match(readouts, /Devices/);
    assert.match(readouts, /Scheduler/);
  });
});

describe('the tabs', () => {
  it('opens every view without error', async () => {
    const app = await mount();
    after(() => app.close());
    await signIn(app);
    const doc = app.document;

    for (const label of ['Schedules', 'Devices', 'Activity', 'Settings']) {
      const tab = findByText(doc, '.tab', label);
      assert.ok(tab, `there should be a ${label} tab`);
      tab.click();
      await app.settle();
      assert.ok(doc.querySelector('.view').children.length > 0,
        `${label} should render something`);
      assert.deepEqual(app.errors, [], `${label} should not throw`);
    }
  });

  it('loads a schedule into the editor and saves it', async () => {
    const app = await mount();
    after(() => app.close());
    await signIn(app);
    findByText(app.document, '.tab', 'Schedules').click();
    await app.settle();
    const editor = app.document.querySelector('textarea');
    assert.ok(editor, 'the schedule editor should be on screen');

    findByText(app.document, 'button', 'Save').click();
    await app.settle();
    const saved = app.server.calls.find(
      (call) => call.method === 'PUT' && call.pathname.endsWith('/schedule'));
    assert.ok(saved, 'saving should reach the daemon');
  });

  it('lays the schedules page out in three columns', async () => {
    const app = await mount();
    after(() => app.close());
    await signIn(app);
    findByText(app.document, '.tab', 'Schedules').click();
    await app.settle();
    const columns = [...app.document.querySelectorAll(
      '.split.schedules > .column')];
    assert.equal(columns.length, 3, 'there should be three columns');
    const [devices, editor, firings] = columns;
    assert.ok(devices.querySelector('.picker'),
      'the device list should stay in the first column');
    assert.equal(devices.querySelector('textarea'), null,
      'the editor should have left the device column');
    assert.ok(editor.querySelector('textarea'),
      'the editor should be in the middle column');
    assert.match(text(editor), /The language/,
      'the language reference should stay in the middle column');
    assert.ok(editor.querySelector('textarea').compareDocumentPosition(
      findByText(editor, '.block', 'The language'))
      & app.window.Node.DOCUMENT_POSITION_FOLLOWING,
    'the editor should sit above the language reference');
    assert.match(text(firings), /Next five firings/,
      'the firings should be in the right-hand column');
  });

  it('shows the inventory and can add a device', async () => {
    const app = await mount();
    after(() => app.close());
    await signIn(app);
    findByText(app.document, '.tab', 'Devices').click();
    await app.settle();

    const host = app.document.querySelector('input[placeholder="192.168.1.40"]');
    set(app.window, host, '10.0.0.9');
    findByText(app.document, 'button', 'Add device').click();
    await app.settle();
    const added = app.server.calls.find(
      (call) => call.method === 'POST' && call.pathname === '/api/devices');
    assert.ok(added, 'the device should have been posted');
  });

  it('lists settings with the values the daemon reported', async () => {
    const app = await mount();
    after(() => app.close());
    await signIn(app);
    findByText(app.document, '.tab', 'Settings').click();
    await app.settle();
    const body = text(app.document.querySelector('.view'));
    assert.match(body, /listen port/);
    assert.match(body, /PAM service/);
    assert.equal(app.document.querySelector('input[type="password"]').value,
      '', 'the device password must never be prefilled');
  });
});

describe('the daemon account', () => {
  it('opens on the panel when a proper user is set', async () => {
    const app = await mount();
    after(() => app.close());
    await signIn(app);
    assert.ok(app.document.querySelector('.card'),
      'the panel should be the landing page');
    assert.equal(app.document.querySelector('.banner-alarm'), null);
  });

  // Running as nobody is a misconfiguration the operator has to see, so
  // the panel puts them in front of it rather than waiting to be found.
  it('lands on settings and warns when running as nobody', async () => {
    const app = await mount({lastResort: true});
    after(() => app.close());
    await signIn(app);

    const banner = app.document.querySelector('.banner-alarm');
    assert.ok(banner, 'a warning banner should be on screen');
    assert.match(text(banner), /nobody/);
    assert.match(text(banner), /Set a daemon user/i);

    const current = findByText(app.document, '.tab.is-current', 'Settings');
    assert.ok(current, 'the settings tab should be the current one');
    assert.deepEqual(app.errors, []);
  });

  it('lets the operator leave the settings page', async () => {
    const app = await mount({lastResort: true});
    after(() => app.close());
    await signIn(app);
    findByText(app.document, '.tab', 'Panel').click();
    await app.settle();
    assert.ok(app.document.querySelector('.card'),
      'the redirect must happen once, not on every poll');
  });

  it('summarises the certificate with a countdown', async () => {
    const app = await mount();
    after(() => app.close());
    await signIn(app);
    findByText(app.document, '.tab', 'Settings').click();
    await app.settle();

    const card = app.document.querySelector('.block.certificate');
    assert.ok(card, 'a certificate card should be on the settings page');
    const body = text(card);
    assert.match(body, /panel\.example\.com/);
    assert.match(body, /Let's Encrypt/);
    assert.match(body, /expires in/);
    // The countdown renders a duration, not a raw date.
    assert.match(body, /\d+d \d+h \d+m/);
    assert.match(body, /Reloaded 2 time/);

    // It sits under the files card, as asked.
    const blocks = [...app.document.querySelectorAll('.block')];
    const files = blocks.indexOf(app.document.querySelector('.block.files'));
    const certificate = blocks.indexOf(card);
    assert.ok(certificate > files,
      'the certificate card should come after the files card');
  });

  it('warns about files it will not be able to write', async () => {
    const app = await mount();
    after(() => app.close());
    await signIn(app);
    findByText(app.document, '.tab', 'Settings').click();
    await app.settle();

    const box = app.document.querySelector('.banner-warn');
    assert.ok(box, 'a warnings box should be on the settings page');
    const body = text(box);
    assert.match(body, /2 files/);
    assert.match(body, /\/var\/log\/panel\.log/);
    assert.match(body, /privkey\.pem/);
    // Whether the path was chosen or inherited is part of the answer.
    assert.match(body, /configured/);
    // The usable ones are not listed.
    assert.doesNotMatch(body, /config\.json/);

    // It goes across the bottom, after the settings and the cards.
    const view = app.document.querySelector('.view > div');
    assert.equal(view.lastElementChild, box,
      'the warnings box should be the last thing on the page');
  });

  it('shows which account and helper are in use', async () => {
    const app = await mount();
    after(() => app.close());
    await signIn(app);
    findByText(app.document, '.tab', 'Settings').click();
    await app.settle();
    const body = text(app.document.querySelector('.view'));
    assert.match(body, /kasapanel/);
    assert.match(body, /separate privileged process/);
    assert.match(body, /daemon user/);
  });
});

// The lock, from the browser's side.  The daemon is the thing that
// enforces it; what these cases check is that the panel asks before it
// sends, and that it does not send when the answer is no.
describe('a locked device', () => {
  function plate(app) {
    return app.document.querySelector('.modal.plate');
  }

  function answer(app, word) {
    findByText(plate(app), 'button', word).click();
    return app.settle();
  }

  it('wears a lock on its card and in the picker', async () => {
    const app = await mount({locked: true});
    after(() => app.close());
    await signIn(app);
    const name = app.document.querySelector('.card-name');
    assert.match(text(name), /\u{1F512}/u,
      'the card title should carry a lock beside the name');
    assert.ok(name.querySelector('[aria-label="locked"]'),
      'the lock should be labelled for a screen reader');
  });

  it('asks before switching, and sends nothing on no', async () => {
    const app = await mount({locked: true});
    after(() => app.close());
    await signIn(app);
    app.document.querySelector('.rocker').click();
    await app.settle();

    assert.ok(plate(app), 'the rocker should raise a confirmation');
    assert.match(text(plate(app)), /Switch on Bench plug\?/);
    assert.equal(
      app.server.calls.filter((c) => c.pathname.endsWith('/action')).length,
      0, 'nothing should have been sent yet');

    await answer(app, 'No');
    assert.equal(plate(app), null, 'no should close the plate');
    assert.equal(
      app.server.calls.filter((c) => c.pathname.endsWith('/action')).length,
      0, 'no should mean no');
  });

  it('switches once the answer is yes', async () => {
    const app = await mount({locked: true});
    after(() => app.close());
    await signIn(app);
    findByText(app.document, '.card-actions button', 'Toggle').click();
    await app.settle();
    assert.match(text(plate(app)), /Toggle Bench plug\?/);

    await answer(app, 'Yes');
    assert.equal(plate(app), null, 'the plate should close on success');
    const sent = app.server.calls.find(
      (c) => c.pathname.endsWith('/action'));
    assert.ok(sent, 'the action should have been sent');
    assert.equal(sent.body.action, 'toggle');
    assert.equal(sent.body.confirm, true,
      'the request should say it was confirmed');
  });

  it('asks before snoozing', async () => {
    const app = await mount({locked: true});
    after(() => app.close());
    await signIn(app);
    findByText(app.document, '.card-actions button', 'Snooze').click();
    await app.settle();
    findByText(app.document, '.menu button', '15 minutes').click();
    await app.settle();
    assert.match(text(plate(app)), /Snooze Bench plug for 15min\?/);
    await answer(app, 'Yes');
    const sent = app.server.calls.find(
      (c) => c.pathname.endsWith('/snooze'));
    assert.equal(sent.body.for, '15min');
    assert.equal(sent.body.confirm, true);
  });

  it('asks before saving a schedule', async () => {
    const app = await mount({locked: true});
    after(() => app.close());
    await signIn(app);
    findByText(app.document, '.tab', 'Schedules').click();
    await app.settle();
    findByText(app.document, 'button', 'Save').click();
    await app.settle();

    assert.match(text(plate(app)), /Save the schedule for Bench plug\?/);
    const before = app.server.calls.filter(
      (c) => c.method === 'PUT' && c.pathname.endsWith('/schedule'));
    assert.equal(before.length, 0, 'nothing should be saved unasked');
    await answer(app, 'Yes');
    const after_ = app.server.calls.filter(
      (c) => c.method === 'PUT' && c.pathname.endsWith('/schedule'));
    assert.equal(after_.length, 1);
    assert.equal(after_[0].body.confirm, true);
  });

  it('names disabling a schedule as what it is', async () => {
    const app = await mount({locked: true});
    after(() => app.close());
    await signIn(app);
    findByText(app.document, '.tab', 'Schedules').click();
    await app.settle();
    app.document.querySelector('.toggle-line input').click();
    await app.settle();
    findByText(app.document, 'button', 'Save').click();
    await app.settle();
    assert.match(text(plate(app)),
      /Disable the schedule for Bench plug\?/);
  });

  it('asks before the checkbox on the devices page lets go', async () => {
    const app = await mount({locked: true});
    after(() => app.close());
    await signIn(app);
    findByText(app.document, '.tab', 'Devices').click();
    await app.settle();

    const box = app.document.querySelector('.lock-box');
    assert.ok(box, 'each device should have a lock checkbox');
    assert.equal(box.checked, true, 'a locked device shows as locked');
    // It sits to the left of the buttons for that device.
    const cells = [...box.closest('tr').querySelectorAll('td')];
    const mine = cells.indexOf(box.closest('td'));
    const buttons = cells.findIndex((cell) => cell.querySelector('button'));
    assert.ok(mine < buttons,
      'the checkbox should come before the buttons');

    box.click();
    await app.settle();
    assert.match(text(plate(app)), /Unlock Bench plug\?/);
    const before = app.server.calls.filter((c) => c.method === 'PATCH');
    assert.equal(before.length, 0, 'nothing should be sent unasked');
    await answer(app, 'Yes');
    const sent = app.server.calls.find((c) => c.method === 'PATCH');
    assert.equal(sent.body.locked, false);
    assert.equal(sent.body.confirm, true);
    assert.equal(app.document.querySelector('.lock-box').checked, false);
  });

  it('locks a device without asking anything', async () => {
    const app = await mount();
    after(() => app.close());
    await signIn(app);
    findByText(app.document, '.tab', 'Devices').click();
    await app.settle();
    app.document.querySelector('.lock-box').click();
    await app.settle();
    assert.equal(plate(app), null, 'locking needs no ceremony');
    const sent = app.server.calls.find((c) => c.method === 'PATCH');
    assert.equal(sent.body.locked, true);
  });
});

// secure_locked is not a setting the panel can read, so the dialog
// learns about it the only way anything can: by being asked.
describe('a device locked behind a password', () => {
  function plate(app) {
    return app.document.querySelector('.modal.plate');
  }

  it('asks for the password only once it has to', async () => {
    const app = await mount({locked: true, securePassword: 'secret'});
    after(() => app.close());
    await signIn(app);
    app.document.querySelector('.rocker').click();
    await app.settle();

    assert.equal(plate(app).querySelector('input[type="password"]'), null,
      'the password should not be asked for before it is needed');
    findByText(plate(app), 'button', 'Yes').click();
    await app.settle();

    const field = plate(app).querySelector('input[type="password"]');
    assert.ok(field, 'the dialog should now ask for the password');
    assert.equal(app.document.activeElement, field,
      'the field should be ready to type into');
    const actions = app.server.calls.filter(
      (c) => c.pathname.endsWith('/action'));
    assert.equal(actions.length, 1, 'the first yes was the one refused');
  });

  it('refuses a wrong password and keeps asking', async () => {
    const app = await mount({locked: true, securePassword: 'secret'});
    after(() => app.close());
    await signIn(app);
    app.document.querySelector('.rocker').click();
    await app.settle();
    findByText(plate(app), 'button', 'Yes').click();
    await app.settle();

    set(app.window, plate(app).querySelector('input[type="password"]'),
      'wrong');
    findByText(plate(app), 'button', 'Yes').click();
    await app.settle();
    assert.ok(plate(app), 'the dialog should still be open');
    assert.match(text(plate(app).querySelector('.confirm-problem')),
      /did not match/);
    assert.equal(app.document.querySelector('.rocker')
      .getAttribute('aria-pressed'), 'false',
    'the device should not have been switched');
  });

  it('acts once the password is right', async () => {
    const app = await mount({locked: true, securePassword: 'secret'});
    after(() => app.close());
    await signIn(app);
    app.document.querySelector('.rocker').click();
    await app.settle();
    findByText(plate(app), 'button', 'Yes').click();
    await app.settle();
    set(app.window, plate(app).querySelector('input[type="password"]'),
      'secret');
    findByText(plate(app), 'button', 'Yes').click();
    await app.settle();

    assert.equal(plate(app), null, 'the dialog should close');
    const sent = app.server.calls.filter(
      (c) => c.pathname.endsWith('/action'));
    assert.equal(sent.length, 2, 'asked once, then confirmed');
    assert.equal(sent[1].body.password, 'secret');
    assert.equal(app.document.querySelector('.rocker')
      .getAttribute('aria-pressed'), 'true');
  });
});

describe('signing out', () => {
  it('returns to the sign-in plate', async () => {
    const app = await mount();
    after(() => app.close());
    await signIn(app);
    findByText(app.document, 'button', 'Sign out').click();
    await app.settle();
    assert.ok(app.document.querySelector('form.plate'));
  });
});

// The dark theme.  It is a per-user choice kept by the daemon, so what
// matters here is that the shell is dressed from the file rather than
// from the browser's own taste, and that a change is written back.
describe('the dark theme', () => {
  function toggle(app) {
    return app.document.querySelector('.theme-toggle');
  }

  function saved(app) {
    return app.server.calls.filter(
      (call) => call.pathname === '/api/preferences'
        && call.method === 'PUT');
  }

  it('opens light, and says which way it is set', async () => {
    const app = await mount();
    after(() => app.close());
    await signIn(app);
    assert.equal(
      app.document.documentElement.getAttribute('data-theme'), 'light');
    assert.equal(toggle(app).getAttribute('aria-pressed'), 'false');
    assert.match(text(toggle(app)), /Light/);
    assert.equal(toggle(app).getAttribute('aria-label'), 'Dark mode');
  });

  it('sits to the right of the host clock', async () => {
    const app = await mount();
    after(() => app.close());
    await signIn(app);
    const strip = [...app.document.querySelectorAll('.readouts .readout')];
    const clock = strip.findIndex(
      (node) => text(node).startsWith('Host clock'));
    const theme = strip.findIndex((node) => node.querySelector(
      '.theme-toggle'));
    assert.ok(clock >= 0, 'the host clock should be on the strip');
    assert.ok(theme > clock,
      'the theme switch should come after the host clock');
    assert.equal(theme, strip.length - 1,
      'and nothing should come after it');
  });

  it('dresses the shell and writes the choice down', async () => {
    const app = await mount();
    after(() => app.close());
    await signIn(app);
    toggle(app).click();
    await app.settle();

    assert.equal(
      app.document.documentElement.getAttribute('data-theme'), 'dark');
    assert.equal(toggle(app).getAttribute('aria-pressed'), 'true');
    assert.match(text(toggle(app)), /Dark/);
    const sent = saved(app);
    assert.equal(sent.length, 1, 'the choice should have been saved once');
    assert.equal(sent[0].body.preferences.theme, 'dark');
    assert.equal(app.server.preferences.theme, 'dark',
      'and the daemon should be holding it');
    assert.deepEqual(app.errors, []);
  });

  it('opens dark when that is what the file says', async () => {
    const app = await mount({theme: 'dark'});
    after(() => app.close());
    await signIn(app);
    assert.equal(
      app.document.documentElement.getAttribute('data-theme'), 'dark');
    assert.equal(toggle(app).getAttribute('aria-pressed'), 'true');
    assert.equal(saved(app).length, 0,
      'reading a preference is not changing one');
  });

  // Without this the next load of a dark panel starts light and then
  // flinches once the session is read.
  it('leaves the answer where the next load can find it', async () => {
    const app = await mount({theme: 'dark'});
    after(() => app.close());
    await signIn(app);
    assert.equal(app.window.localStorage.getItem('kasapanel.theme'), 'dark');
  });

  it('turns back to light again', async () => {
    const app = await mount({theme: 'dark'});
    after(() => app.close());
    await signIn(app);
    toggle(app).click();
    await app.settle();
    assert.equal(
      app.document.documentElement.getAttribute('data-theme'), 'light');
    assert.equal(app.server.preferences.theme, 'light');
  });
});

// Rearranging the cards.  The order is the operator's own, kept by the
// daemon per user, so these cases watch both the row on screen and what
// is sent to be remembered.
describe('rearranging the cards', () => {
  const BENCH = '3f7c9d1a-5b24-4e18-9c0f-2a6d8e4b7103';
  const HALL = '7c1e4b80-2d6a-4f13-8b05-91ad3e6c7f22';
  const SHED = 'b5390af6-41c7-4d2e-ae18-6f70c2d9b483';

  function names(app) {
    return [...app.document.querySelectorAll('.card .card-name')]
      .map((node) => text(node));
  }

  function cards(app) {
    return [...app.document.querySelectorAll('.card')];
  }

  function orders(app) {
    return app.server.calls.filter(
      (call) => call.pathname === '/api/preferences'
        && call.method === 'PUT')
      .map((call) => call.body.preferences.device_card_order);
  }

  // jsdom has no drag of its own, so the three events a browser sends
  // are sent by hand, with the clipboard a real one would carry.
  async function drag(app, from, onto) {
    const parcel = {
      store: {},
      effectAllowed: '',
      dropEffect: '',
      setData(kind, value) { this.store[kind] = String(value); },
      getData(kind) { return this.store[kind] || ''; },
    };
    const fire = async (node, kind) => {
      const event = new app.window.Event(
        kind, {bubbles: true, cancelable: true});
      event.dataTransfer = parcel;
      node.dispatchEvent(event);
      await app.settle();
    };
    await fire(from.querySelector('.card-head'), 'dragstart');
    await fire(onto, 'dragover');
    await fire(onto, 'drop');
    return parcel;
  }

  it('takes hold of a card by its header', async () => {
    const app = await mount({extraCards: 2});
    after(() => app.close());
    await signIn(app);
    const head = app.document.querySelector('.card .card-head');
    assert.equal(head.getAttribute('draggable'), 'true',
      'the header is the handle');
    const grip = head.querySelector('.card-grip');
    assert.ok(grip, 'and it shows a grip');
    assert.match(grip.getAttribute('aria-label'), /Move Bench plug/);
    assert.equal(
      app.document.querySelector('.rocker').getAttribute('draggable'),
      'false', 'a hand on the rocker is throwing it, not moving the card');
  });

  it('draws the cards in the order the file remembers', async () => {
    const app = await mount({extraCards: 2, cardOrder: [SHED, HALL, BENCH]});
    after(() => app.close());
    await signIn(app);
    assert.deepEqual(names(app), ['Shed heater', 'Hall lamp', 'Bench plug']);
    assert.equal(orders(app).length, 0,
      'reading an order is not changing one');
  });

  it('puts a device the order has never seen at the end', async () => {
    const app = await mount({extraCards: 2, cardOrder: [HALL]});
    after(() => app.close());
    await signIn(app);
    assert.deepEqual(names(app), ['Hall lamp', 'Bench plug', 'Shed heater'],
      'a new device is noticed at the end, not shuffled into the middle');
  });

  it('moves a card where it was dropped and saves the order', async () => {
    const app = await mount({extraCards: 2});
    after(() => app.close());
    await signIn(app);
    assert.deepEqual(names(app), ['Bench plug', 'Hall lamp', 'Shed heater']);

    const parcel = await drag(app, cards(app)[0], cards(app)[2]);
    assert.equal(parcel.getData('text/plain'), BENCH,
      'the drag should carry the device, for browsers that insist');
    assert.deepEqual(names(app), ['Hall lamp', 'Shed heater', 'Bench plug'],
      'the card should land where the one it was dropped on stood');
    assert.deepEqual(orders(app), [[HALL, SHED, BENCH]]);
    assert.deepEqual(app.server.preferences.device_card_order,
      [HALL, SHED, BENCH]);
    assert.deepEqual(app.errors, []);
  });

  it('moves a card back to the front again', async () => {
    const app = await mount({extraCards: 2});
    after(() => app.close());
    await signIn(app);
    await drag(app, cards(app)[2], cards(app)[0]);
    assert.deepEqual(names(app), ['Shed heater', 'Bench plug', 'Hall lamp']);
    assert.deepEqual(orders(app), [[SHED, BENCH, HALL]]);
  });

  it('saves nothing when a card is dropped on itself', async () => {
    const app = await mount({extraCards: 2});
    after(() => app.close());
    await signIn(app);
    await drag(app, cards(app)[1], cards(app)[1]);
    assert.deepEqual(names(app), ['Bench plug', 'Hall lamp', 'Shed heater']);
    assert.deepEqual(orders(app), [], 'nothing moved, so nothing to save');
  });

  it('marks the card in the hand and the one it would displace',
    async () => {
      const app = await mount({extraCards: 2});
      after(() => app.close());
      await signIn(app);
      const event = new app.window.Event(
        'dragstart', {bubbles: true, cancelable: true});
      event.dataTransfer = {setData() {}, effectAllowed: ''};
      cards(app)[0].querySelector('.card-head').dispatchEvent(event);
      await app.settle();
      assert.ok(cards(app)[0].className.includes('is-moving'));

      const over = new app.window.Event(
        'dragover', {bubbles: true, cancelable: true});
      over.dataTransfer = {dropEffect: ''};
      cards(app)[2].dispatchEvent(over);
      await app.settle();
      assert.ok(cards(app)[2].className.includes('is-landing'));
      assert.ok(!cards(app)[1].className.includes('is-landing'));
    });

  // A rearrangement that needs a mouse is one half the operators cannot
  // do at all.
  it('moves a card with the arrow keys', async () => {
    const app = await mount({extraCards: 2});
    after(() => app.close());
    await signIn(app);
    const grip = cards(app)[0].querySelector('.card-grip');
    grip.dispatchEvent(new app.window.KeyboardEvent(
      'keydown', {key: 'ArrowRight', bubbles: true, cancelable: true}));
    await app.settle();
    assert.deepEqual(names(app), ['Hall lamp', 'Bench plug', 'Shed heater']);
    assert.deepEqual(orders(app), [[HALL, BENCH, SHED]]);
  });

  it('leaves a card at the end of the row where it is', async () => {
    const app = await mount({extraCards: 2});
    after(() => app.close());
    await signIn(app);
    const grip = cards(app)[0].querySelector('.card-grip');
    grip.dispatchEvent(new app.window.KeyboardEvent(
      'keydown', {key: 'ArrowLeft', bubbles: true, cancelable: true}));
    await app.settle();
    assert.deepEqual(names(app), ['Bench plug', 'Hall lamp', 'Shed heater']);
    assert.deepEqual(orders(app), [], 'it was already first');
  });
});
