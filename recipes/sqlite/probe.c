#include <sqlite3.h>
int main(void) {
    sqlite3 *db = 0;
    sqlite3_stmt *query = 0;
    if (sqlite3_open(":memory:", &db) != SQLITE_OK) return 1;
    int result = sqlite3_exec(db, "CREATE TABLE test(value); INSERT INTO test VALUES(42)", 0, 0, 0);
    if (result == SQLITE_OK) result = sqlite3_prepare_v2(db, "SELECT value FROM test", -1, &query, 0);
    if (result == SQLITE_OK) result = sqlite3_step(query) == SQLITE_ROW && sqlite3_column_int(query, 0) == 42 ? SQLITE_OK : SQLITE_ERROR;
    sqlite3_finalize(query);
    sqlite3_close(db);
    return result != SQLITE_OK || !sqlite3_threadsafe();
}
