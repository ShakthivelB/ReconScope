Add a persistent "Pin URL" feature to ReconScope's URL Explorer.

Goal:
I want to manually pin individual URLs during recon so I can easily find them later.

Requirements:

1. Add a persistent pinned/bookmarked state to URLs.
2. Add a pin icon/button to every URL card.
3. Unpinned state:
   ☆ Pin
4. Pinned state:
   ★ Pinned
5. Clicking the pin must immediately toggle the state without navigating away from the URL Explorer.
6. Persist the state in SQLite so it survives browser refreshes and application restarts.
7. Add a "Pinned Only" filter to URL Explorer.
8. The filter must work together with existing:
   - search
   - status filters
   - Interesting Only
   - sorting
   - pagination
9. Pinned URLs must remain pinned when:
   - HTTP status is checked
   - URL is rescanned
   - page is refreshed
   - application restarts
10. Do not change the existing HTTP status scanner.
11. Do not change the Web Preview implementation.
12. Do not create a new "Scans & Diffs" section.
13. Do not add browser localStorage as the primary persistence mechanism; use SQLite.
14. Do not create duplicate URL records.
15. Existing URLs should default to unpinned.
16. Existing imported URLs must remain intact.

UI:
Add the pin control near the URL metadata/actions, for example:

☆ Pin

When pinned:

★ Pinned

Add:

📌 Pinned Only

to the existing URL Explorer filter controls.

Keep the current dark ReconScope design and mobile responsiveness.

Backend:
- Add the required SQLAlchemy model field/table.
- Add an API endpoint to toggle or set pinned state.
- Return pinned state in URL API responses.
- Make the endpoint safe/idempotent.
- Handle URL-not-found cleanly.

Frontend:
- Render pin state from backend data.
- Toggle without a full page reload.
- Update the icon/state immediately after a successful request.
- Show an appropriate error if the API request fails.
- Preserve the current Web Preview and Details accordions.

Database:
- Do NOT wipe the existing database.
- Existing URLs must remain.
- Existing URLs should receive pinned=false.

Testing:
- Pin one existing URL.
- Refresh the page and verify it remains pinned.
- Enable Pinned Only and verify only pinned URLs appear.
- Unpin it and verify it disappears from Pinned Only.
- Verify pagination still works.
- Verify search + Pinned Only works together.
- Verify Interesting Only + Pinned Only works together.

Before modifying anything, inspect the current URL model, URL API, URL Explorer API, frontend JS, and database initialization so the implementation follows the existing architecture.

After implementation, tell me exactly which files were changed.
