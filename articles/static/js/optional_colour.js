/* Keeps an OptionalColourWidget's picker and text box in step.
   Delegated from document so it also works for panels Wagtail adds later.

   The text box takes a colour written any way CSS understands it
   (#1d5fc4, #FC0, fc0, rgb(…), hsl(…), "gold"): the browser itself reads
   it, and the box is rewritten as #rrggbb when you leave it, which is
   what gets saved. */
(function () {
    var canvas = null;

    function toHex(value) {
        var text = (value || '').trim();
        if (!text) return '';
        var bare = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(text);
        if (bare) {
            var digits = bare[1].toLowerCase();
            if (digits.length === 3) digits = digits.replace(/./g, '$&$&');
            return '#' + digits;
        }
        // Let the browser parse anything else: an unknown colour leaves the
        // canvas's fill style at the sentinel.
        canvas = canvas || document.createElement('canvas').getContext('2d');
        var sentinel = '#010203';
        canvas.fillStyle = sentinel;
        canvas.fillStyle = text;
        var resolved = canvas.fillStyle;
        if (resolved === sentinel && text.toLowerCase() !== sentinel) return null;
        if (resolved.charAt(0) === '#') return resolved;
        var parts = resolved.match(/[\d.]+/g);          // rgba(r, g, b, a)
        if (!parts || parts.length < 3) return null;
        return '#' + parts.slice(0, 3).map(function (n) {
            return ('0' + Math.round(Number(n)).toString(16)).slice(-2);
        }).join('');
    }

    function parts(target) {
        if (!target.closest) return null;
        var wrapper = target.closest('.optional-colour');
        if (!wrapper) return null;
        var swatch = wrapper.querySelector('.optional-colour__swatch');
        var text = wrapper.querySelector('.optional-colour__text');
        return swatch && text ? { swatch: swatch, text: text } : null;
    }

    document.addEventListener('input', function (event) {
        var p = parts(event.target);
        if (!p) return;
        if (event.target === p.swatch) {
            p.text.value = p.swatch.value;
            p.text.setCustomValidity('');
            p.text.dispatchEvent(new Event('change', { bubbles: true }));
        } else if (event.target === p.text) {
            var hex = toHex(p.text.value);
            if (hex) p.swatch.value = hex;
            p.text.setCustomValidity(hex === null ? 'Not a colour the browser recognises.' : '');
        }
    });

    // Leaving the box writes the colour the way it will be saved.
    document.addEventListener('change', function (event) {
        var p = parts(event.target);
        if (!p || event.target !== p.text) return;
        var hex = toHex(p.text.value);
        if (hex !== null && hex !== p.text.value) p.text.value = hex;
    });
})();
