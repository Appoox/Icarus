/* reader/static/js/admin_login.js */

document.addEventListener('DOMContentLoaded', function() {
    const usernameInput = document.getElementById('id_username');
    if (usernameInput && !document.getElementById('id_username_0')) {
        const parent = usernameInput.parentElement;
        
        const container = document.createElement('div');
        container.style.display = 'flex';
        container.style.gap = '10px';
        container.style.width = '100%';
        
        const select = document.createElement('select');
        select.id = 'id_username_0';
        select.className = 'input';
        select.style.width = '100px';
        select.style.flexShrink = '0';
        select.style.backgroundColor = '#222';
        select.style.color = 'white';
        select.style.border = '1px solid #444';
        select.style.borderRadius = '4px';
        select.style.height = '3rem';
        
        // Comprehensive list of country codes
        const countryCodes = [
            "+91", "+1", "+44", "+971", "+966", "+965", "+968", "+974", "+973", 
            "+61", "+1", "+49", "+33", "+39", "+81", "+86", "+7", "+34", "+55", 
            "+27", "+65", "+60", "+64", "+31", "+41", "+46", "+47", "+45", "+353"
        ].sort((a, b) => {
            if (a === "+91") return -1;
            if (b === "+91") return 1;
            return parseInt(a.slice(1)) - parseInt(b.slice(1));
        });

        countryCodes.forEach(code => {
            const option = document.createElement('option');
            option.value = code;
            option.text = code;
            if (code === "+91") option.selected = true;
            select.appendChild(option);
        });
        
        parent.insertBefore(container, usernameInput);
        container.appendChild(select);
        container.appendChild(usernameInput);
        
        usernameInput.style.flexGrow = '1';
        usernameInput.placeholder = 'Phone Number';
        
        const form = usernameInput.closest('form');
        form.addEventListener('submit', function() {
            let val = usernameInput.value.trim();
            if (val && !val.startsWith('+')) {
                usernameInput.value = select.value + val;
            }
        });
    }
});
