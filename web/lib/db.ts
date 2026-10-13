import mysql from "mysql2/promise";

// Parse DATABASE_URL (mysql://user:pass@host:port/db) en champs explicites
// pour éviter toute ambiguïté sur l'option `uri` de mysql2.
function poolConfig(): mysql.PoolOptions {
  const url = new URL(process.env.DATABASE_URL ?? "mysql://root@localhost:3306/prospection");
  return {
    host: url.hostname,
    port: url.port ? Number(url.port) : 3306,
    user: decodeURIComponent(url.username),
    password: decodeURIComponent(url.password),
    database: url.pathname.replace(/^\//, ""),
    connectionLimit: 5,
    timezone: "Z", // interprète les DATETIME stockés en UTC
    // mysql2 parse automatiquement les colonnes JSON en objets/tableaux JS.
  };
}

const globalForDb = globalThis as unknown as { mysqlPool?: mysql.Pool };

export const pool = globalForDb.mysqlPool ?? mysql.createPool(poolConfig());

if (process.env.NODE_ENV !== "production") globalForDb.mysqlPool = pool;

export async function query<T = any>(sql: string, params: any[] = []): Promise<T[]> {
  const [rows] = await pool.query(sql, params);
  return rows as T[];
}

export async function queryOne<T = any>(sql: string, params: any[] = []): Promise<T | null> {
  const rows = await query<T>(sql, params);
  return rows[0] ?? null;
}

/** Exécute `fn` dans une transaction (tout ou rien) : COMMIT si `fn` réussit, ROLLBACK sinon. */
export async function transaction<T>(fn: (q: (sql: string, params?: any[]) => Promise<any[]>) => Promise<T>): Promise<T> {
  const conn = await pool.getConnection();
  try {
    await conn.beginTransaction();
    const out = await fn(async (sql, params = []) => { const [rows] = await conn.query(sql, params); return rows as any[]; });
    await conn.commit();
    return out;
  } catch (e) {
    await conn.rollback().catch(() => {});
    throw e;
  } finally {
    conn.release();
  }
}
