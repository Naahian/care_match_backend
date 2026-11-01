class FakeUserProfiles:
    def __init__(self):
        self.rows = {}
        self.writes = []
        self.deleted = []

    def get(self, uid):
        return dict(self.rows[uid]) if uid in self.rows else None

    def save(self, uid, changes):
        self.writes.append((uid, dict(changes)))
        self.rows[uid] = {**self.rows.get(uid, {}), **changes}

    def delete(self, uid):
        self.deleted.append(uid)
        self.rows.pop(uid, None)
