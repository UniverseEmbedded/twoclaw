// reset_vectordb.js
// 向量数据库重置工具 - 用于强制清理和重建

const fs = require('fs').promises;
const path = require('path');
const readline = require('readline');

const VECTOR_STORE_PATH = path.join(__dirname, 'VectorStore');
const DB_PATH = path.join(VECTOR_STORE_PATH, 'vectordb.sqlite');

/**
 * 创建交互式命令行接口
 */
function createInterface() {
    return readline.createInterface({
        input: process.stdin,
        output: process.stdout
    });
}

/**
 * 询问用户确认
 */
function askConfirmation(question) {
    const rl = createInterface();
    return new Promise((resolve) => {
        rl.question(question + ' (yes/no): ', (answer) => {
            rl.close();
            resolve(answer.toLowerCase() === 'yes' || answer.toLowerCase() === 'y');
        });
    });
}

/**
 * 显示当前数据库状态
 */
async function showDatabaseStatus() {
    console.log('\n' + '='.repeat(60));
    console.log('向量数据库状态检查');
    console.log('='.repeat(60));
    
    try {
        const dbExists = await fileExists(DB_PATH);
        
        if (dbExists) {
            const stats = await fs.stat(DB_PATH);
            const sizeMB = (stats.size / 1024 / 1024).toFixed(2);
            console.log(`✓ 数据库文件存在: ${DB_PATH}`);
            console.log(`  大小: ${sizeMB} MB`);
            console.log(`  修改时间: ${stats.mtime.toLocaleString()}`);
            
            // 检查索引文件
            const files = await fs.readdir(VECTOR_STORE_PATH);
            const indexFiles = files.filter(f => f.endsWith('.bin'));
            const mapFiles = files.filter(f => f.endsWith('_map.json'));
            
            console.log(`\n索引文件统计:`);
            console.log(`  .bin文件: ${indexFiles.length} 个`);
            console.log(`  _map.json文件: ${mapFiles.length} 个 (旧格式)`);
        } else {
            console.log('⚠ 数据库文件不存在');
        }
    } catch (error) {
        console.error('❌ 检查失败:', error.message);
    }
    
    console.log('='.repeat(60) + '\n');
}

/**
 * 重置选项1：仅删除数据库文件（保留索引）
 */
async function resetDatabaseOnly() {
    console.log('\n[选项1] 仅删除数据库文件');
    console.log('说明: 删除vectordb.sqlite，保留.bin索引文件');
    console.log('用途: 修复数据库损坏，但保留已计算的向量');
    console.log('结果: 下次启动时会自动重建数据库表结构\n');
    
    const confirmed = await askConfirmation('确认删除数据库文件？');
    if (!confirmed) {
        console.log('操作已取消');
        return;
    }
    
    try {
        if (await fileExists(DB_PATH)) {
            await fs.unlink(DB_PATH);
            console.log('✓ 数据库文件已删除');
            
            // 删除WAL文件
            const walPath = DB_PATH + '-wal';
            const shmPath = DB_PATH + '-shm';
            if (await fileExists(walPath)) await fs.unlink(walPath);
            if (await fileExists(shmPath)) await fs.unlink(shmPath);
            
            console.log('✓ 重置完成！下次启动时将自动重建数据库');
        } else {
            console.log('⚠ 数据库文件不存在，无需删除');
        }
    } catch (error) {
        console.error('❌ 删除失败:', error.message);
    }
}

/**
 * 重置选项2：删除指定日记本的数据
 */
async function resetSpecificDiary() {
    const rl = createInterface();
    
    return new Promise((resolve) => {
        rl.question('\n请输入要重置的日记本名称: ', async (diaryName) => {
            rl.close();
            
            if (!diaryName.trim()) {
                console.log('❌ 日记本名称不能为空');
                resolve();
                return;
            }
            
            console.log(`\n准备重置日记本: "${diaryName}"`);
            const confirmed = await askConfirmation('确认删除此日记本的所有数据？');
            
            if (!confirmed) {
                console.log('操作已取消');
                resolve();
                return;
            }
            
            try {
                // 删除索引文件
                const safeFileName = Buffer.from(diaryName, 'utf-8').toString('base64url');
                const indexPath = path.join(VECTOR_STORE_PATH, `${safeFileName}.bin`);
                
                if (await fileExists(indexPath)) {
                    await fs.unlink(indexPath);
                    console.log(`✓ 已删除索引文件: ${safeFileName}.bin`);
                }
                
                // 删除旧的map文件（如果存在）
                const mapPath = path.join(VECTOR_STORE_PATH, `${safeFileName}_map.json`);
                if (await fileExists(mapPath)) {
                    await fs.unlink(mapPath);
                    console.log(`✓ 已删除map文件: ${safeFileName}_map.json`);
                }
                
                console.log('\n⚠ 注意: 数据库中的记录需要在下次启动时自动清理');
                console.log('或者使用选项3完全重置数据库');
                
            } catch (error) {
                console.error('❌ 删除失败:', error.message);
            }
            
            resolve();
        });
    });
}

/**
 * 重置选项3：完全重置（删除所有数据）
 */
async function resetEverything() {
    console.log('\n[选项3] 完全重置向量数据库');
    console.log('⚠️  警告: 这将删除所有向量数据和索引！');
    console.log('说明: 删除数据库文件和所有.bin索引文件');
    console.log('用途: 完全清理后从零开始重建');
    console.log('结果: 所有日记本都需要重新向量化\n');
    
    const confirmed = await askConfirmation('确认完全重置？此操作不可恢复！');
    if (!confirmed) {
        console.log('操作已取消');
        return;
    }
    
    const doubleCheck = await askConfirmation('最后确认：真的要删除所有数据吗？');
    if (!doubleCheck) {
        console.log('操作已取消');
        return;
    }
    
    try {
        let deletedCount = 0;
        
        // 1. 删除数据库文件
        if (await fileExists(DB_PATH)) {
            await fs.unlink(DB_PATH);
            deletedCount++;
            console.log('✓ 已删除: vectordb.sqlite');
        }
        
        // 删除WAL文件
        const walPath = DB_PATH + '-wal';
        const shmPath = DB_PATH + '-shm';
        if (await fileExists(walPath)) {
            await fs.unlink(walPath);
            deletedCount++;
        }
        if (await fileExists(shmPath)) {
            await fs.unlink(shmPath);
            deletedCount++;
        }
        
        // 2. 删除所有索引文件
        const files = await fs.readdir(VECTOR_STORE_PATH);
        const indexFiles = files.filter(f => f.endsWith('.bin'));
        
        for (const file of indexFiles) {
            await fs.unlink(path.join(VECTOR_STORE_PATH, file));
            deletedCount++;
        }
        
        console.log(`✓ 已删除 ${indexFiles.length} 个索引文件`);
        
        // 3. 删除旧的JSON文件（如果存在）
        const jsonFiles = ['manifest.json', 'usage_stats.json', 'diary_name_vectors.json', 'failed_rebuilds.json'];
        const mapFiles = files.filter(f => f.endsWith('_map.json'));
        
        for (const file of [...jsonFiles, ...mapFiles]) {
            const filePath = path.join(VECTOR_STORE_PATH, file);
            if (await fileExists(filePath)) {
                await fs.unlink(filePath);
                deletedCount++;
            }
        }
        
        console.log(`\n✓ 完全重置完成！共删除 ${deletedCount} 个文件`);
        console.log('✓ 下次启动时将自动重建所有数据');
        
    } catch (error) {
        console.error('❌ 重置失败:', error.message);
    }
}

/**
 * 备份数据库
 */
async function backupDatabase() {
    console.log('\n[选项4] 备份当前数据库');
    
    if (!await fileExists(DB_PATH)) {
        console.log('⚠ 数据库文件不存在，无需备份');
        return;
    }
    
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-').split('T')[0];
    const backupPath = path.join(VECTOR_STORE_PATH, `vectordb_backup_${timestamp}.sqlite`);
    
    try {
        await fs.copyFile(DB_PATH, backupPath);
        const stats = await fs.stat(backupPath);
        console.log(`✓ 备份完成: ${path.basename(backupPath)}`);
        console.log(`  大小: ${(stats.size / 1024 / 1024).toFixed(2)} MB`);
    } catch (error) {
        console.error('❌ 备份失败:', error.message);
    }
}

/**
 * 检查文件是否存在
 */
async function fileExists(filePath) {
    try {
        await fs.access(filePath);
        return true;
    } catch {
        return false;
    }
}

/**
 * 主菜单
 */
async function showMenu() {
    console.log('\n' + '='.repeat(60));
    console.log('向量数据库重置工具');
    console.log('='.repeat(60));
    console.log('\n请选择操作:');
    console.log('  0. 显示数据库状态');
    console.log('  1. 仅删除数据库文件（保留索引）');
    console.log('  2. 删除指定日记本的数据');
    console.log('  3. 完全重置（删除所有数据）⚠️');
    console.log('  4. 备份当前数据库');
    console.log('  5. 退出');
    console.log('');
    
    const rl = createInterface();
    
    rl.question('请输入选项 (0-5): ', async (choice) => {
        rl.close();
        
        switch (choice.trim()) {
            case '0':
                await showDatabaseStatus();
                await showMenu();
                break;
            case '1':
                await resetDatabaseOnly();
                await showMenu();
                break;
            case '2':
                await resetSpecificDiary();
                await showMenu();
                break;
            case '3':
                await resetEverything();
                process.exit(0);
                break;
            case '4':
                await backupDatabase();
                await showMenu();
                break;
            case '5':
                console.log('\n再见！');
                process.exit(0);
                break;
            default:
                console.log('\n❌ 无效选项，请重新选择');
                await showMenu();
        }
    });
}

// 启动
if (require.main === module) {
    console.clear();
    showMenu().catch(error => {
        console.error('程序错误:', error);
        process.exit(1);
    });
}

module.exports = {
    resetDatabaseOnly,
    resetSpecificDiary,
    resetEverything,
    backupDatabase
};