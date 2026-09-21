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
    assert.match(versions, /kasapanel 1\.7\.0/);
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
