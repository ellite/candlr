import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../src/pages/settings.astro', import.meta.url), 'utf8');
const handlers = source.slice(source.indexOf('    editModal.addEventListener("keydown"'), source.indexOf('    const deleteEventTypeModal'));

for (const detail of ['Event type already exists', 'A start year is required']) {
  test(`rejected save preserves focus, keyboard navigation, and drafts: ${detail}`, async () => {
    const document = { activeElement: null };
    function control(value = '') {
      const listeners = {};
      let disabled = false;
      const el = { value, tabIndex: 0, listeners, focus() { document.activeElement = el; },
        getClientRects: () => [1], addEventListener: (name, fn) => { listeners[name] = fn; } };
      Object.defineProperty(el, 'disabled', { get: () => disabled, set(value) {
        disabled = value;
        if (value && document.activeElement === el) document.activeElement = null;
      } });
      return el;
    }
    const editName = control('Duplicate');
    const editInterval = control('2');
    const editUnit = control('year');
    const close = control();
    const editSave = control();
    const controls = [close, editName, editInterval, editUnit, editSave];
    let hidden = false;
    let errorHidden = true;
    const editModal = { listeners: {}, classList: { contains: () => hidden },
      contains: (el) => controls.includes(el), querySelectorAll: () => controls,
      addEventListener(name, fn) { this.listeners[name] = fn; } };
    const editError = { textContent: '', classList: { add() { errorHidden = true; }, remove() { errorHidden = false; } } };
    let rowsChanged = false;
    const editTrigger = { dataset: { editEventType: '1' }, closest() { rowsChanged = true; throw new Error('unexpected row mutation'); } };
    vm.runInNewContext(handlers, { document, editModal, editName, editInterval, editUnit, editSave, editError, editTrigger,
      m: {}, fetch: async () => {
        assert.equal(document.activeElement, editName, 'focus stays in modal while saving');
        assert.equal(editSave.disabled, true);
        return { ok: false, json: async () => ({ detail }) };
      }, closeEditModal() { hidden = true; } });
    editSave.focus();
    await editSave.listeners.click();
    assert.equal(document.activeElement, editName);
    assert.equal(editSave.disabled, false);
    assert.equal(editError.textContent, detail);
    assert.equal(errorHidden, false);
    assert.equal(rowsChanged, false);
    assert.deepEqual([editName.value, editInterval.value, editUnit.value], ['Duplicate', '2', 'year']);
    const key = (key, shiftKey = false) => {
      let prevented = false;
      editModal.listeners.keydown({ key, shiftKey, preventDefault() { prevented = true; } });
      return prevented;
    };
    editSave.focus();
    assert.equal(key('Tab'), true);
    assert.equal(document.activeElement, close);
    assert.equal(key('Tab', true), true);
    assert.equal(document.activeElement, editSave);
    assert.equal(key('Escape'), true);
    assert.equal(hidden, true);
  });
}
