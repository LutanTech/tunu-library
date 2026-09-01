const Wishlist = {
    STORAGE_KEY: 'tunu_wishlist',

    get() {
        try {
            return JSON.parse(localStorage.getItem(this.STORAGE_KEY)) || [];
        } catch (e) {
            return [];
        }
    },

    save(list) {
        localStorage.setItem(this.STORAGE_KEY, JSON.stringify(list));
        window.dispatchEvent(new CustomEvent('wishlist-updated', { detail: list }));
    },

    add(book) {
        const list = this.get();
        if (!list.find(b => b.id === book.id)) {
            list.push({
                id: book.id,
                title: book.title,
                authors: book.authors,
                image: book.image,
                added_at: new Date().toISOString()
            });
            this.save(list);
            this.sync();
            return true;
        }
        return false;
    },

    remove(bookId) {
        let list = this.get();
        list = list.filter(b => b.id !== bookId);
        this.save(list);
        // We don't necessarily remove from backend on local remove, 
        // but we could if we wanted a full mirror.
        return true;
    },

    async sync() {
        const list = this.get();
        if (list.length === 0) return;

        // Check if user is logged in (could check a global var or cookie)
        const isLoggedIn = document.body.dataset.loggedIn === 'true';
        if (!isLoggedIn) return;

        try {
            const response = await fetch('/api/wishlist/sync', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ wishlist: list })
            });
            if (response.ok) {
                console.log('Wishlist synced with backend');
            }
        } catch (e) {
            console.error('Failed to sync wishlist', e);
        }
    },

    init() {
        // Initial sync on load
        this.sync();
    }
};

document.addEventListener('DOMContentLoaded', () => Wishlist.init());
